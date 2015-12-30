# -*- coding: utf-8 -*-
"Main module defining filesystem and file classes"
import os
import subprocess
import warnings
import lib
from lib import ensure_byte, ct

def get_default_host():
    "Try to guess the namenode by looking in this machine's hadoop conf."
    confd = os.environ.get('HADOOP_CONF_DIR', os.environ.get('HADOOP_INSTALL',
                           '') + '/hadoop/conf')
    host = open(os.sep.join([confd, 'masters'])).readlines()[1][:-1]
    return host


def init_kerb():
    """Uses system kinit to find credentials. Set up by editing
    krb5.conf"""
    raise NotImplementedError("Please set your credentials manually")
    out1 = subprocess.check_call(['kinit'])
    out2 = subprocess.check_call(['klist'])
    HDFileSystem.ticket_cache = None
    HDFileSystem.token = None


class HDFileSystem():
    "A connection to an HDFS namenode"
    _handle = None
    host = get_default_host()
    port = 9000
    user = None
    ticket_cache = None
    token = None
    pars = {}
    _token = None  # Delegation token (generated)
    autoconnect = True
    
    def __init__(self, **kwargs):
        """
        Parameters
        ----------
        
        host : str (default from config files)
            namenode (name or IP)
            
        port : int (9000)
            connection port
            
        user, ticket_cache, token : str
            kerberos things
            
        pars : {str: str}
            other parameters for hadoop
        """
        for arg in kwargs:
            setattr(self, arg, kwargs[arg])
        # self.__dict__.update(kwargs)
        if self.autoconnect:
            self.connect()
    
    def connect(self):
        assert self._handle is None, "Already connected"
        o = lib.hdfsNewBuilder()
        lib.hdfsBuilderSetNameNodePort(o, self.port)
        lib.hdfsBuilderSetNameNode(o, ensure_byte(self.host))
        if self.user:
            lib.hdfsBuilderSetUserName(o, ensure_byte(self.user))
        if self.ticket_cache:
            lib.hdfsBuilderSetKerbTicketCachePath(o, ensure_byte(self.ticket_cache))
        if self.token:
            lib.hdfsBuilderSetToken(o, ensure_byte(self.token))
        if self.pars:
            for par in self.pars:
                try:
                    assert lib.hdfsBuilderConfSetStr(o, ensure_byte(par),
                                          ensure_byte(self.pars(par))) == 0
                except AssertionError:
                    warnings.warn('Setting conf parameter %s failed' % par)
        fs = lib.hdfsBuilderConnect(o)
        if fs:
            self._handle = fs
            #if self.token:   # TODO: find out what a delegation token is
            #    self._token = lib.hdfsGetDelegationToken(self._handle,
            #                                             ensure_byte(self.user))
        else:
            raise RuntimeError('Connection Failed')
    
    def disconnect(self):
        if self._handle:
            lib.hdfsDisconnect(self._handle)
        self._handle = None
    
    def open(self, path, mode='r', **kwargs):
        assert self._handle, "Filesystem not connected"
        return HDFile(self, path, mode, **kwargs)
        
    def get_block_locations(self, path, start=0, length=None):
        "Fetch physical locations of blocks"
        assert self._handle, "Filesystem not connected"
        fi = self.info(path)
        length = length or fi['size']
        nblocks = ct.c_int(0)
        out = lib.hdfsGetFileBlockLocations(self._handle, ensure_byte(path),
                                ct.c_int64(start), ct.c_int64(length),
                                ct.byref(nblocks))
        locs = []
        for i in range(nblocks.value):
            block = out[i]
            hosts = [block.hosts[i] for i in
                     range(block.numOfNodes)]
            locs.append({'hosts': hosts, 'length': block.length,
                         'offset': block.offset})
        lib.hdfsFreeFileBlockLocations(out, nblocks)
        return locs

    def info(self, path):
        "File information"
        fi = lib.hdfsGetPathInfo(self._handle, ensure_byte(path)).contents
        out = struct_to_dict(fi)
        lib.hdfsFreeFileInfo(ct.byref(fi), 1)
        return out
    
    def ls(self, path):
        num = ct.c_int(0)
        fi = lib.hdfsListDirectory(self._handle, ensure_byte(path), ct.byref(num))
        out = [struct_to_dict(fi[i]) for i in range(num.value)]
        lib.hdfsFreeFileInfo(fi, num.value)
        return out
    
    def __repr__(self):
        state = ['Disconnected', 'Connected'][self._handle is not None]
        return 'hdfs://%s:%s, %s' % (self.host, self.port, state)
    
    def __del__(self):
        if self._handle:
            self.disconnect()
    
    def mkdir(self, path):
        out = lib.hdfsCreateDirectory(self._handle, ensure_byte(path))
        return out == 0
        
    def set_replication(self, path, repl):
        out = lib.hdfsSetReplication(self._handle, ensure_byte(path),
                                     ct.c_int16(repl))
        return out == 0
    
    def mv(self, path1, path2):
        out = lib.hdfsRename(self._handle, ensure_byte(path1), ensure_byte(path2))
        return out == 0

    def rm(self, path, recursive=True):
        "Use recursive for `rm -r`, i.e., delete directory and contents"
        out = lib.hdfsDelete(self._handle, ensure_byte(path), recursive)
        return out == 0
    
    def exists(self, path):
        out = lib.hdfsExists(self._handle, ensure_byte(path) )
        return out == 0
    
    def truncate(self, path, pos):
        # Does not appear to ever succeed
        out = lib.hdfsTruncate(self._handle, ensure_byte(path),
                               ct.c_int64(pos), 0)
        return out == 0
    
    def chmod(self, path, mode):
        "Mode in numerical format (give as octal, if convenient)"
        out = lib.hdfsChmod(self._handle, ensure_byte(path), ct.c_short(mode))
        return out == 0
    
    def chown(self, path, owner, group):
        out = lib.hdfsChown(self._handle, ensure_byte(path), ensure_byte(owner),
                            ensure_byte(group))
        return out == 0


def struct_to_dict(s):
    return dict((name, getattr(s, name)) for (name, p) in s._fields_)


class HDFile():
    _handle = None
    fs = None
    _fs = None
    path = None
    mode = None
    
    def __init__(self, fs, path, mode, repl=1, offset=0, buff=0):
        "Called by open on a HDFileSystem"
        self.fs = fs
        self.path = path
        self.repl = repl
        self._fs = fs._handle
        m = {'w': 1, 'r': 0, 'a': 1025}[mode]
        self.mode = mode
        out = lib.hdfsOpenFile(self._fs, ensure_byte(path), m, buff,
                            ct.c_short(repl), ct.c_int64(offset))
        if out == 0:
            raise IOError("File open failed")
        self._handle = out
    
    def read(self, length=2**16):
        "Read, in chunks no bigger than the native filesystem (e.g., 64kb)"
        assert lib.hdfsFileIsOpenForRead(self._handle), 'File not read mode'
        # TODO: read in chunks greater than block size by multiple
        # calls to read
        # TODO: consider tell() versuss filesize to determine bytes available.
        p = ct.create_string_buffer(length)
        ret = lib.hdfsRead(self._fs, self._handle, p, ct.c_int32(length))
        if ret >= 0:
            return p.raw[:ret]
        else:
            raise IOError('Read Failed:', -ret)
    
    def tell(self):
        out = lib.hdfsTell(self._fs, self._handle)
        if out == -1:
            raise IOError('Tell Failed')
        return out
    
    def seek(self, loc):
        out = lib.hdfsSeek(self._fs, self._handle, ct.c_int64(loc))
        if out == -1:
            raise IOError('Seek Failed')
    
    def info(self):
        "filesystem metadata about this file"
        return self.fs.info(self.path)
    
    def write(self, data):
        data = ensure_byte(data)
        assert lib.hdfsFileIsOpenForWrite(self._handle), 'File not write mode'
        assert lib.hdfsWrite(self._fs, self._handle, data, len(data)) == len(data)
        
    def flush(self):
        lib.hdfsFlush(self._fs, self._handle)
    
    def close(self):
        self.flush()
        lib.hdfsCloseFile(self._fs, self._handle)
        self._handle = None  # libhdfs releases memory
        self.mode = 'closed'
            
    def get_block_locs(self):
        return self.fs.get_block_locations(self.path)

    def __del__(self):
        self.close()

    def __repr__(self):
        return 'hdfs://%s:%s%s, %s' % (self.fs.host, self.fs.port,
                                            self.path, self.mode)
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()

def test():
    fs = HDFileSystem()
    with fs.open('/newtest', 'w', repl=1) as f:
        import time
        data = b'a' * (1024 * 2**20)
        t0 = time.time()
        f.write(data)
    t1 = time.time()
    with fs.open('/newtest', 'r') as f:
        out = 1
        while out:
            out = f.read(2**16)
    print(fs)
    print(f)
    print(fs.info(f.path))
    print(t1 - t0)
    print(time.time() - t1)
    print(subprocess.check_output("hadoop fs -ls /newtest", shell=True))
    print(f.get_block_locs())
    fs.rm(f.path)
