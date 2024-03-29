#
# -*-encoding:gb2312-*-

import os
import hashlib

from twisted.internet import reactor, defer

from bitfield import Bitfield

from tools import sleep

class BTFileError (Exception) :
    pass

class BTHashTestError (Exception):
    pass

class BTFile:
    def __init__(self, metainfo, index, saveDir):
        fileinfo = metainfo.files[index]
        piece_len = metainfo.piece_length
        self.fileInfo = fileinfo
        self.path = os.path.join(saveDir, fileinfo['path'])
        self.length = fileinfo['length']
        self.piece_len = piece_len

        self.abs_pos0, self.abs_pos1 = fileinfo['pos_range']

        self.fd = None

        idx0, ext = divmod(self.abs_pos0, self.piece_len)
        self.idx0_piece = idx0

        idx1, ext = divmod(self.abs_pos1, self.piece_len)
        self.idx1_piece = idx1+1 if ext else idx1
        
        #print self.abs_pos0, self.abs_pos1, self.piece_len, self.idx0_piece, self.idx1_piece

        h, t = os.path.split(self.path)
        if not os.path.exists(h):
            os.makedirs(h)

    def __str__(self):
        return u'piece=[{},{}) size={:,d} "{}" '.format(self.idx0_piece, self.idx1_piece, self.length, os.path.split(self.path)[1]).encode('gb2312')

    def __getIntersection(self, index, beg, data_len):
        # p0,p1,f0,f1 absolute position in files
        p0 = index * self.piece_len + beg
        p1 = p0 + data_len

        f0, f1 = self.abs_pos0, self.abs_pos1

        # intersect sub piece
        pf0 = max(p0, f0)
        pf1 = min(p1, f1)

        # pb,pe relative positioin in piece
        pb = pf0 - p0
        pe = pf1 - p0

        # fb,fe relative position in current file
        fb = pf0 - f0
        fe = pf1 - f0

        return (pb, pe), (fb, fe)


    def write(self, index, beg, data):
        (pb,pe), (fb,fe) = self.__getIntersection(index, beg, len(data))

        if pb >= pe :
            raise BTFileError("index isn't in this file")

        my_data = data[pb:pe]

        # print len(my_data)

        if self.fd is None :
            if os.path.exists(self.path) :
                length = os.path.getsize(self.path)
                if length != self.length:
                    raise BTFileError(u'old file size is error: {}'.format(self.path))
                fd = open(self.path, 'rb+')
            else :
                fd = open(self.path, 'wb+')
                fd.truncate(self.length)

            self.fd = fd

        self.fd.seek(fb)

        self.fd.write(my_data)

        return pb, len(my_data)

    def read(self, index, beg, data_len):
        (pb,pe), (fb,fe) = self.__getIntersection(index, beg, data_len)

        #print pb, pe, fb, fe

        if pb >= pe :
            raise BTFileError("index isn't in this file")

        if self.fd is None:
            try:
                self.fd = open(self.path, 'rb+')
            except IOError as error:
                raise BTFileError(str(error))

        self.fd.seek(fb)
        data = self.fd.read(fe-fb)
        return pb, data

    def close(self):
        if self.fd :
            self.fd.close()
    
    def __getitem__(self, idx):
        return self.read(idx, 0, self.piece_len)

    def __setitem__(self, idx, data):
        self.write(idx, 0, data)

    def __iter__(self) :
        for idx in xrange(self.idx0_piece, self.idx1_piece) :
            yield idx, self[idx]

    def __len__(self) :
        return self.idx1_piece - self.idx0_piece

    def __contains__(self, idx) :
        # (pb,pe), (fb,fe) = self.__getIntersection(index, 0, self.piece_len)
        # return pb < pe
        return self.idx0_piece <= idx < self.idx1_piece


class BTFiles :
    def __init__(self, metainfo, saveDir, selectedFileIndex=None):
        if selectedFileIndex is None :
            selectedFileIndex = range(len(metainfo.files))
        selectedFileIndex.sort()
        
        self.metainfo = metainfo
        self.saveDir = saveDir
        self.totalSize = metainfo.total_length
        self.pieceNum = metainfo.pieces_size
        self.pieceLength = metainfo.piece_length
        self.hashArray = metainfo.pieces_hash
        
        self.files = []
        for i in selectedFileIndex :
            self.files.append(BTFile(metainfo, i, saveDir))

    def doHashTest(self, idx, data):
        return hashlib.sha1(data).digest() == self.hashArray[idx]

    def getBitfield(self) :
        bfNeed = Bitfield(self.pieceNum)
        for f in self.files :
            for i in xrange(f.idx0_piece, f.idx1_piece) :
                bfNeed[i] = 1

        bfHave = Bitfield(self.pieceNum)
        for i in xrange(self.pieceNum):
            try :
                ds = self[i]
                if len(ds) == 1:
                    beg, dat = ds[0]
                    if self.doHashTest(i, dat):
                        bfHave[i] = 1
                        bfNeed[i] = 0
            except BTFileError as error :
                pass

        return bfHave, bfNeed

    def write(self, idx, data) :
        ds = [f.write(idx,0,data) for f in self.files if idx in f]
        if len(ds) <= 1 :
            return ds
        else :
            _ds = ds[0:1]
            for d in ds[1:] :
                beg0, len0 = _ds[-1]
                beg1, len1 = d
                assert beg0+len0 <= beg1
                if beg0+len0==beg1:
                    _ds[-1] = beg0, len0+len1
                else:
                    _ds.append(d)
            return _ds
            
    def __getitem__(self, idx) :
        # ds = [f[idx] for f in self.files if idx in f]
        ds = []
        for f in self.files:
            if idx in f:
                try:
                    ds.append(f[idx])
                except BTFileError as error:
                    pass

        if len(ds) <=1 :
            return ds
        else :
            _ds = ds[0:1]
            for d in ds[1:] :
                beg0, dat0 = _ds[-1]
                beg1, dat1 = d
                assert beg0+len(dat0) <= beg1
                if beg0+len(dat0)==beg1:
                    _ds[-1] = beg0, dat0+dat1
                else:
                    _ds.append(d)
            return _ds

    def __setitem__(self, idx, data) :
        for f in self.files:
            if idx in f :
                f[idx] = data

    def __iter__(self):
        for idx in xrange(len(self)) :
            yield idx, self[idx]

    def __contains__(self, idx) :
        return any(idx in f for f in self.files)

    def __len__(self):
        return self.pieceNum

    def __str__(self):
        return '\n'.join(str(f) for f in self.files)
            
class BTFileManager :
    '''
    需要下载的文件
    '''

    slice_size = 2**14

    def __init__(self, btm):
        self.btm = btm
        self.config = btm.config

        metainfo = self.config.metainfo
        self.download_list = self.config.downloadList

        self.metainfo = metainfo
        self.piece_length = metainfo.piece_length
        self.pieceNum = metainfo.pieces_size

        self.btfiles = BTFiles(metainfo, self.config.saveDir, self.config.downloadList)

        self.bitfieldHave, self.bitfieldNeed = self.btfiles.getBitfield()

        print self.config.saveDir
        print self.bitfieldNeed

        self.buffer_reserved = {} # 永驻内存的块，并且单独保存，主要针对文件边界所在的块

        self.buffer_max_size = 100 * 2**20 / self.piece_length # 100M缓冲大小

        #print self.buffer_max_size

    def start(self) :
        self.status = 'started'
        
        self.buffer = {}        # 缓冲piece
        self.buffer_record = []  # 访问先后次序
        self.buffer_dirty = {}  # 需要写入硬盘的piece

        reactor.callLater(10, self.deamon_write)
        reactor.callLater(10, self.deamon_read)

    def stop(self) :
        for idx, data in self.buffer_dirty.iteritems():
            self.write(idx, data)

        self.buffer_dirty.clear()

        self.buffer.clear()

        del self.buffer_record[:]

        self.status = 'stopped'

    @defer.inlineCallbacks
    def deamon_write(self):
        while self.status == 'started':
            self.__thread_write()
            yield sleep(10)
    
    def __thread_write(self):
        if not hasattr(self, '__thread_write_status') :
            self.__thread_write_status = 'stopped'

        if self.__thread_write_status == 'running' :
            return

        if not self.buffer_dirty :
            return

        bfd = self.buffer_dirty.copy()

        def call_in_thread():
            print '--O-O write to disk', len(bfd), bfd.keys()
            for idx in sorted(bfd.keys()) :
                data = bfd[idx]
                self.write(idx, data)
            reactor.callFromThread(call_from_thread)

        def call_from_thread():
            self.__thread_write_status = 'stopped'
            for idx, data in bfd.iteritems() :
                if data is self.buffer_dirty[idx] :
                    #print '删除写回硬盘的数据'
                    del self.buffer_dirty[idx]

        if self.__thread_write_status == 'stopped' :
            self.__thread_write_status = 'running'
            reactor.callInThread(call_in_thread)

    @defer.inlineCallbacks
    def deamon_read(self):
        while self.status == 'started':
            size = len(self.buffer)
            if size > self.buffer_max_size :
                remove_count = size - self.buffer_max_size
                remove_count += self.buffer_max_size / 5
                for idx in self.buffer_record[:remove_count] :
                    del self.buffer[idx]
                del self.buffer_record[:remove_count]

            yield sleep(10)

    ############################################################
    # 高层操作 buffer

    def readPiece(self, index) :
        if not (0 <= index < self.pieceNum) :
            raise BTFileError('index is out of range')
        if not self.bitfieldHave[index] :
            raise BTFileError('index is not downloaded')

        if index in self.buffer :
            data = self.buffer[index]
            self.buffer_record.remove(index)
            self.buffer_record.append(index)
            return data

        else:
            for idx in [index-1, index, index+1] :
                if 0 <= idx < self.pieceNum and idx not in self.buffer :
                    data = self.read(idx)
                    assert data
                    self.buffer[idx] = data
                    self.buffer_record.append(idx)
                    #print 'index =', idx

            data = self.readPiece(index)

            return data
            
    def writePiece(self, index, piece) :
        if not (0 <= index < self.pieceNum) :
            raise BTFileError('index is out of range')
        if not self.bitfieldNeed[index] :
            raise BTFileError('index is not need')

        if not self.btfiles.doHashTest(index, piece):
            raise BTHashTestError()

        else:
            self.bitfieldHave[index] = 1
            self.bitfieldNeed[index] = 0

            if index in self.buffer :
                self.buffer[index] = piece

            self.buffer_dirty[index] = piece

            # self.write(index, piece)

            return True

    ############################################################
    # 针对piece的读写，操作 buffer_dirty, buffer_reserved

    def read(self, index):
        if index in self.buffer_dirty:
            return self.buffer_dirty[index]
        elif index in self.buffer_reserved :
            return self.buffer_reserved[index]

        data_list = self.btfiles[index]

        if len(data_list) == 1 :
            assert data_list[0][0] == 0
            return data_list[0][1]
        else:
            assert False
            return data_list

    def write(self, index, data) :
        ds = self.btfiles.write(index, data)
        if len(ds) > 1 : # 文件边界，但是相邻文件用户并没有选择下载
            # print len(data), length
            # assert False
            self.buffer_reserved[index] = data # 保存该数据
        elif not ds :
            assert False
        
    def __iter__(self):
        return self.btfiles.__iter__()

if __name__ == '__main__':
    from MetaInfo import BTMetaInfo
    from bitfield import Bitfield
    from random import randint
    from app import BTConfig
    from BTManager import BTManager
    
    metainfo = BTMetaInfo('test.torrent')

    # bfm = BTFileManager(metainfo)

    # bfm.start()

    # def _read(idx):
    #     return bfm.readPiece(idx)

    # def _write(idx, data):
    #     return bfm.writePiece(idx, data)

    bfs = BTFiles(metainfo, 'C:/')
    def _read(idx):
        res = bfs[idx]
        assert len(res) == 1 and res[0][0] == 0
        return res[0][1]

    def _write(idx, data):
        bfs[idx] = data
    
    pieceNum = len(bfs)
    pieceLen = bfs.pieceLength

    char = []
    for i in xrange(pieceNum) :
        c = chr(randint(0, 0xFF))
        _len = pieceLen if i != pieceNum-1 else metainfo.last_piece_length
        data = c * _len
        _write(i, data)
        char.append(c)

    for i in xrange(pieceNum - 1):
        c = char[i]
        _data = c * pieceLen
        data = _read(i)
        #print i, len(data), ord(c)
        assert _data == data

    # c = char[-1]

    # _data = c * metainfo.last_piece_length
    
    # idx, beg, data = bfm.readPiece(bfm.pieces_size-1)
    # print bfm.pieces_size-1, len(data), ord(c)
    # assert _data == data

    def read_write():
        # write
        i = randint(0, pieceNum-1)
        c = chr(randint(0, 0xFF))
        char[i] = c

        if i == pieceNum-1:
            _len = metainfo.last_piece_length
        else:
            _len = metainfo.piece_length

        _data = c * _len

        _write(i, _data)

        print '<<< ', i, len(_data)
        
        # read

        i = randint(0, pieceNum-1)
        
        c = char[i]

        if i == pieceNum-1:
            _len = metainfo.last_piece_length
        else:
            _len = metainfo.piece_length
        
        _data = c * _len

        data = _read(i)

        print '>>>', i, len(data), _len, ord(c), ord(data[0])

        assert _data == data

        #print 'length, ', len(bfm.buffer_dirty), len(bfm.buffer), len(bfm.buffer_reserved)
        
        reactor.callLater(0, read_write)

    read_write()

    reactor.run()
