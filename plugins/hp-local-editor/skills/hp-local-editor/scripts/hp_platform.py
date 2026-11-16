"""Operating-system boundary for the HP helpers: macOS/Linux and Windows.

Site files are always opened relative to a pinned parent directory, never by
re-resolving a path: POSIX uses O_NOFOLLOW + dir_fd; Windows uses NtCreateFile
relative to held directory handles with FILE_OPEN_REPARSE_POINT, rejects
reparse points (symlinks, junctions) from each opened handle, creates the
temporary file in the held directory and renames it by handle. Renaming or
replacing a path component meanwhile cannot redirect a read or write outside.
"""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import subprocess
import uuid

WINDOWS = os.name == 'nt'
# Text-mode descriptors on Windows rewrite newlines; every helper write is binary.
O_BINARY = getattr(os, 'O_BINARY', 0)
O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
_REPARSE_POINT = 0x400
_DIRECTORY = 0x10

if WINDOWS:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class _FileInformation(ctypes.Structure):
        _fields_ = [('dwFileAttributes', wintypes.DWORD), ('ftCreationTime', wintypes.FILETIME),
                    ('ftLastAccessTime', wintypes.FILETIME), ('ftLastWriteTime', wintypes.FILETIME),
                    ('dwVolumeSerialNumber', wintypes.DWORD), ('nFileSizeHigh', wintypes.DWORD),
                    ('nFileSizeLow', wintypes.DWORD), ('nNumberOfLinks', wintypes.DWORD),
                    ('nFileIndexHigh', wintypes.DWORD), ('nFileIndexLow', wintypes.DWORD)]

    class _UnicodeString(ctypes.Structure):
        _fields_ = [('Length', wintypes.USHORT), ('MaximumLength', wintypes.USHORT), ('Buffer', wintypes.LPWSTR)]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [('Length', wintypes.ULONG), ('RootDirectory', wintypes.HANDLE),
                    ('ObjectName', ctypes.POINTER(_UnicodeString)), ('Attributes', wintypes.ULONG),
                    ('SecurityDescriptor', ctypes.c_void_p), ('SecurityQualityOfService', ctypes.c_void_p)]

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]

    class _DispositionInfo(ctypes.Structure):
        _fields_ = [('DeleteFile', ctypes.c_ubyte)]

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    _kernel32.GetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.POINTER(_FileInformation))
    _kernel32.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _ntdll = ctypes.WinDLL('ntdll')
    _ntdll.NtCreateFile.restype = ctypes.c_long
    _ntdll.NtCreateFile.argtypes = (ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, ctypes.POINTER(_ObjectAttributes),
                                    ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
                                    wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG)
    _ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
    _ntdll.RtlNtStatusToDosError.argtypes = (ctypes.c_long,)
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _GENERIC_READ, _GENERIC_WRITE = 0x80000000, 0x40000000
    _FILE_GENERIC_READ, _FILE_GENERIC_WRITE, _DELETE, _SYNCHRONIZE = 0x120089, 0x120116, 0x10000, 0x100000
    _READ_ATTRIBUTES = 0x80  # needed by GetFileInformationByHandle on every checked handle
    _LIST_DIRECTORY = 0x1 | 0x80  # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES
    _SHARE_ALL = 0x1 | 0x2 | 0x4
    _CREATE_NEW, _OPEN_EXISTING, _OPEN_ALWAYS = 1, 3, 4
    _BACKUP_SEMANTICS, _OPEN_REPARSE_POINT = 0x02000000, 0x00200000
    # NtCreateFile dispositions and options.
    _NT_OPEN, _NT_CREATE = 1, 2
    _NT_DIRECTORY, _NT_NON_DIRECTORY, _NT_SYNCHRONOUS = 0x1, 0x40, 0x20
    _NT_OPEN_REPARSE_POINT = 0x00200000
    _OBJ_CASE_INSENSITIVE = 0x40
    _FILE_RENAME_INFO, _FILE_DISPOSITION_INFO = 3, 4
else:
    import fcntl


def is_link_like(info):
    """A symlink, or on Windows any reparse point such as a junction."""
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & _REPARSE_POINT)


def is_link_path(path):
    """Path.is_symlink that also treats Windows junctions as links."""
    try:
        return is_link_like(os.lstat(path))
    except FileNotFoundError:
        return False


def _win_checked(handle, name):
    """Return the attributes of an opened handle; close it and raise for a reparse point."""
    try:
        info = _FileInformation()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            error = ctypes.get_last_error()
            raise OSError(None, ctypes.FormatError(error), str(name), error)
        if info.dwFileAttributes & _REPARSE_POINT:
            raise OSError(errno.ELOOP, 'unsafe_link', str(name))
    except BaseException:
        _kernel32.CloseHandle(handle)
        raise
    return info.dwFileAttributes


def _win_open(path, access, share, disposition):
    """CreateFileW on the name itself (never a reparse target); returns (handle, attributes)."""
    handle = _kernel32.CreateFileW(str(path), access, share, None, disposition,
                                   _OPEN_REPARSE_POINT | _BACKUP_SEMANTICS, None)
    if handle is None or handle == _INVALID_HANDLE:
        error = ctypes.get_last_error()
        raise OSError(None, ctypes.FormatError(error), str(path), error)
    return handle, _win_checked(handle, path)


def _nt_open(parent, name, access, share, disposition, options):
    """Open name relative to the parent directory handle without following reparse points."""
    encoded = name.encode('utf-16-le')
    buffer = ctypes.create_unicode_buffer(name)
    text = _UnicodeString(len(encoded), len(encoded) + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent, ctypes.pointer(text),
                                   _OBJ_CASE_INSENSITIVE, None, None)
    handle, status_block = wintypes.HANDLE(), _IoStatusBlock()
    # Unlike CreateFileW, NtCreateFile adds no implicit FILE_READ_ATTRIBUTES | SYNCHRONIZE.
    status = _ntdll.NtCreateFile(ctypes.byref(handle), access | _READ_ATTRIBUTES | _SYNCHRONIZE, ctypes.byref(attributes),
                                 ctypes.byref(status_block), None, 0x80, share, disposition,
                                 options | _NT_SYNCHRONOUS | _NT_OPEN_REPARSE_POINT, None, 0)
    if status < 0:
        error = _ntdll.RtlNtStatusToDosError(status)
        raise OSError(None, ctypes.FormatError(error), name, error)
    return handle.value, _win_checked(handle.value, name)


def _win_descriptor(handle, flags):
    try:
        return msvcrt.open_osfhandle(handle, flags)
    except BaseException:
        _kernel32.CloseHandle(handle)
        raise


def _win_set_information(handle, information_class, value):
    if not _kernel32.SetFileInformationByHandle(handle, information_class, ctypes.byref(value), ctypes.sizeof(value)):
        error = ctypes.get_last_error()
        raise OSError(None, ctypes.FormatError(error), None, error)


def open_private(path, flags, mode=0o600):
    """os.open for helper-owned metadata: never follows or truncates through a final link."""
    if not WINDOWS:
        return os.open(path, flags | O_NOFOLLOW | O_BINARY, mode)
    if flags & os.O_CREAT and flags & os.O_EXCL:
        disposition = _CREATE_NEW
    elif flags & os.O_CREAT:
        disposition = _OPEN_ALWAYS
    else:
        disposition = _OPEN_EXISTING
    access = {os.O_RDONLY: _GENERIC_READ, os.O_WRONLY: _GENERIC_WRITE}.get(
        flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR), _GENERIC_READ | _GENERIC_WRITE)
    handle, attributes = _win_open(path, access, _SHARE_ALL, disposition)
    if attributes & _DIRECTORY:
        _kernel32.CloseHandle(handle)
        raise IsADirectoryError(errno.EISDIR, 'unsafe_metadata', str(path))
    descriptor = _win_descriptor(handle, (flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR | os.O_APPEND)) | O_BINARY)
    if flags & os.O_TRUNC:
        # Truncate only after the opened object is proven to be this regular file.
        os.ftruncate(descriptor, 0)
    return descriptor


def lock_nonblocking(descriptor):
    """Take an exclusive whole-owner lock or raise BlockingIOError immediately."""
    if WINDOWS:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            raise BlockingIOError(errno.EWOULDBLOCK, 'lock_busy') from None
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def close_lock(descriptor):
    """Release a lock taken by lock_nonblocking (a no-op if it was not held)."""
    if WINDOWS:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    os.close(descriptor)


def _parts(relative):
    parts = relative.split('/')
    if not parts or any(part in ('', '.', '..') for part in parts):
        raise ValueError('unsafe_site_path')
    return parts


@contextmanager
def _directory(root, parts):
    """Yield a pinned parent: a POSIX dir fd or a Windows directory handle."""
    if WINDOWS:
        handles = []
        try:
            handle, attributes = _win_open(root, _LIST_DIRECTORY, _SHARE_ALL, _OPEN_EXISTING)
            handles.append(handle)
            if not attributes & _DIRECTORY:
                raise NotADirectoryError(errno.ENOTDIR, 'unsafe_directory', str(root))
            for part in parts:
                handle, _ = _nt_open(handles[-1], part, _LIST_DIRECTORY, _SHARE_ALL, _NT_OPEN, _NT_DIRECTORY)
                handles.append(handle)
            yield handles[-1]
        finally:
            for handle in reversed(handles):
                _kernel32.CloseHandle(handle)
        return
    descriptors = []
    try:
        current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(current)
        for part in parts:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            descriptors.append(current)
        yield current
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_in(directory, name, limit):
    if WINDOWS:
        handle, attributes = _nt_open(directory, name, _FILE_GENERIC_READ, _SHARE_ALL, _NT_OPEN, 0)
        if attributes & _DIRECTORY:
            _kernel32.CloseHandle(handle)
            raise ValueError('not_regular_file')
        descriptor = _win_descriptor(handle, os.O_RDONLY | O_BINARY)
    else:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('not_regular_file')
    except BaseException:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, 'rb') as stream:
        return stream.read(limit + 1)


def _commit(directory, temporary, name):
    """Atomically move the written temporary file over name inside the pinned directory.

    temporary is a name (POSIX) or the open temporary file handle (Windows).
    """
    if WINDOWS:
        encoded = name.encode('utf-16-le')

        # Windows requires BufferSize >= sizeof(FILE_RENAME_INFO) (which already has
        # one WCHAR plus padding) + FileNameLength, so reserve four spare WCHARs.
        class _RenameInfo(ctypes.Structure):
            _fields_ = [('ReplaceIfExists', wintypes.DWORD), ('RootDirectory', wintypes.HANDLE),
                        ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * (len(encoded) // 2 + 4))]
        # The name is resolved relative to the held directory handle, never a path.
        info = _RenameInfo(1, directory, len(encoded))
        ctypes.memmove(ctypes.addressof(info) + _RenameInfo.FileName.offset, encoded, len(encoded))
        _win_set_information(temporary, _FILE_RENAME_INFO, info)
    else:
        os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)


def read_regular(root, relative, limit):
    """Read at most limit+1 bytes of a regular file under root without following links.

    Raises OSError for links or missing paths and ValueError('not_regular_file')
    for directories, devices and other special files.
    """
    parts = _parts(relative)
    with _directory(root, parts[:-1]) as directory:
        return _read_in(directory, parts[-1], limit)


def update_regular(root, relative, limit, decide):
    """Read a regular file and, in the same pinned directory, atomically replace it.

    decide(current_bytes) returns the new bytes or None to leave the file as is.
    """
    parts = _parts(relative)
    with _directory(root, parts[:-1]) as directory:
        data = decide(_read_in(directory, parts[-1], limit))
        if data is None:
            return False
        temporary = '.hp-edit-' + uuid.uuid4().hex
        if WINDOWS:
            handle, _ = _nt_open(directory, temporary, _FILE_GENERIC_WRITE | _DELETE, 0, _NT_CREATE, _NT_NON_DIRECTORY)
            output = _win_descriptor(handle, os.O_WRONLY | O_BINARY)
            committed = False
            try:
                with os.fdopen(output, 'wb', closefd=False) as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                _commit(directory, handle, parts[-1])
                committed = True
            finally:
                if not committed:
                    try:
                        _win_set_information(handle, _FILE_DISPOSITION_INFO, _DispositionInfo(1))
                    except OSError:
                        pass
                os.close(output)
            return True
        created = False
        try:
            output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=directory)
            created = True
            with os.fdopen(output, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            _commit(directory, temporary, parts[-1])
            created = False
        finally:
            if created:
                os.unlink(temporary, dir_fd=directory)
        return True


def replace_regular(root, relative, data):
    """Atomically replace an existing regular file under root with data."""
    return update_regular(root, relative, 0, lambda current: data)


def _windows_identity(pid):
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD))
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        code = wintypes.DWORD()
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            return None
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return None
        size = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            return None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return '%d %d %s' % (pid, created, image.value)
    finally:
        kernel32.CloseHandle(handle)


def _ps(pid, fields):
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', fields], capture_output=True, text=True)
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def process_identity(pid):
    """Identity text (PID, start time, command/image) for a live PID, or None."""
    return _windows_identity(pid) if WINDOWS else _ps(pid, 'pid=,ppid=,lstart=,command=')


def process_record(pid, command):
    """Launch record for a child this helper just started."""
    identity = process_identity(pid)
    if identity is None:
        raise OSError(errno.ESRCH, 'owned_process_unavailable')
    started = identity.split(' ', 2)[1] if WINDOWS else _ps(pid, 'lstart=')
    return {'pid': pid, 'ppid': os.getpid(), 'started_at': started, 'command': command, 'identity': identity}


def terminate_owned(process, identity):
    """Terminate an owned subprocess only while it still has its recorded identity.

    Returns False when it already exited; raises when its identity changed.
    Popen.terminate uses the original handle (Windows) or SIGTERM (POSIX).
    """
    if process.poll() is not None:
        return False
    if process_identity(process.pid) != identity:
        if process.poll() is not None:
            return False
        raise ProcessLookupError('owned_process_identity_changed')
    process.terminate()
    return True
