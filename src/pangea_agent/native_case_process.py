"""Windows-only execution of a compiled probe with a private, capability-free token.

No package/service installation. Only temporary verification paths receive ACLs.
Failure to establish isolation raises; callers must record unverified, never retry
the executable as an unrestricted process.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import time
import uuid


def run_probe(executable: Path, work: Path, source: Path, arguments=(), *, timeout=10, cancelled=lambda: False) -> dict:
    if os.name != 'nt':
        raise OSError('Restricted native verification is available on Windows only')
    import ctypes as c
    from ctypes import wintypes as w

    work, source, executable = work.resolve(strict=True), source.resolve(strict=True), executable.resolve(strict=True)
    if not executable.is_relative_to(work) or work == source or source.is_relative_to(work):
        raise ValueError('Probe and read-only source must use separate verification directories')
    kernel, userenv, advapi = (c.WinDLL(name, use_last_error=True) for name in ('kernel32', 'userenv', 'advapi32'))
    ptr, size = c.c_void_p, c.c_size_t

    class SecurityAttributes(c.Structure):
        _fields_ = [('length', w.DWORD), ('descriptor', ptr), ('inherit', w.BOOL)]

    class Startup(c.Structure):
        _fields_ = [('cb', w.DWORD), ('reserved', w.LPWSTR), ('desktop', w.LPWSTR), ('title', w.LPWSTR),
                    ('x', w.DWORD), ('y', w.DWORD), ('xsize', w.DWORD), ('ysize', w.DWORD),
                    ('xchars', w.DWORD), ('ychars', w.DWORD), ('fill', w.DWORD), ('flags', w.DWORD),
                    ('show', w.WORD), ('reserved2size', w.WORD), ('reserved2', ptr),
                    ('stdin', w.HANDLE), ('stdout', w.HANDLE), ('stderr', w.HANDLE)]

    class StartupEx(c.Structure):
        _fields_ = [('startup', Startup), ('attributes', ptr)]

    class ProcessInfo(c.Structure):
        _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE), ('pid', w.DWORD), ('tid', w.DWORD)]

    class Capabilities(c.Structure):
        _fields_ = [('sid', ptr), ('capabilities', ptr), ('count', w.DWORD), ('reserved', w.DWORD)]

    class BasicLimits(c.Structure):
        _fields_ = [('process_time', c.c_longlong), ('job_time', c.c_longlong), ('flags', w.DWORD),
                    ('min_working', size), ('max_working', size), ('processes', w.DWORD),
                    ('affinity', size), ('priority', w.DWORD), ('scheduling', w.DWORD)]

    class ExtendedLimits(c.Structure):
        _fields_ = [('basic', BasicLimits), ('io', c.c_ulonglong * 6), ('process_memory', size),
                    ('job_memory', size), ('peak_process', size), ('peak_job', size)]

    def api(dll, name, result, arguments):
        function = getattr(dll, name)
        function.restype, function.argtypes = result, arguments
        return function

    create_profile = api(userenv, 'CreateAppContainerProfile', w.LONG, [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, ptr, w.DWORD, c.POINTER(ptr)])
    delete_profile = api(userenv, 'DeleteAppContainerProfile', w.LONG, [w.LPCWSTR])
    sid_string = api(advapi, 'ConvertSidToStringSidW', w.BOOL, [ptr, c.POINTER(w.LPWSTR)])
    free_sid = api(advapi, 'FreeSid', ptr, [ptr])
    local_free = api(kernel, 'LocalFree', ptr, [ptr])
    close = api(kernel, 'CloseHandle', w.BOOL, [w.HANDLE])
    create_file = api(kernel, 'CreateFileW', w.HANDLE, [w.LPCWSTR, w.DWORD, w.DWORD, ptr, w.DWORD, w.DWORD, w.HANDLE])
    create_job = api(kernel, 'CreateJobObjectW', w.HANDLE, [ptr, w.LPCWSTR])
    job_limits = api(kernel, 'SetInformationJobObject', w.BOOL, [w.HANDLE, c.c_int, ptr, w.DWORD])
    assign_job = api(kernel, 'AssignProcessToJobObject', w.BOOL, [w.HANDLE, w.HANDLE])
    terminate_job = api(kernel, 'TerminateJobObject', w.BOOL, [w.HANDLE, w.UINT])
    terminate_process = api(kernel, 'TerminateProcess', w.BOOL, [w.HANDLE, w.UINT])
    init_attributes = api(kernel, 'InitializeProcThreadAttributeList', w.BOOL, [ptr, w.DWORD, w.DWORD, c.POINTER(size)])
    update_attribute = api(kernel, 'UpdateProcThreadAttribute', w.BOOL, [ptr, w.DWORD, size, ptr, size, ptr, ptr])
    delete_attributes = api(kernel, 'DeleteProcThreadAttributeList', None, [ptr])
    create_process = api(kernel, 'CreateProcessW', w.BOOL, [w.LPCWSTR, w.LPWSTR, ptr, ptr, w.BOOL, w.DWORD, ptr, w.LPCWSTR, ptr, ptr])
    resume = api(kernel, 'ResumeThread', w.DWORD, [w.HANDLE])
    wait = api(kernel, 'WaitForSingleObject', w.DWORD, [w.HANDLE, w.DWORD])
    exit_code = api(kernel, 'GetExitCodeProcess', w.BOOL, [w.HANDLE, c.POINTER(w.DWORD)])

    def check(value):
        if not value:
            raise c.WinError(c.get_last_error())
        return value

    name = 'pangea-probe-' + uuid.uuid4().hex
    sid, sid_text, process = ptr(), w.LPWSTR(), ProcessInfo()
    job = output_handle = input_handle = attributes = None
    profile_created = False
    acl_paths = []
    acl_tool = str(Path(os.environ['SystemRoot']) / 'System32/icacls.exe')
    with tempfile.TemporaryDirectory(prefix='pangea-probe-output-') as output_dir:
        output = Path(output_dir) / 'stdout.txt'
        try:
            hr = create_profile(name, name, 'PANGEA temporary case verification', None, 0, c.byref(sid))
            if hr < 0:
                raise OSError(f'Cannot create restricted process identity: 0x{hr & 0xffffffff:08x}')
            profile_created = True
            check(sid_string(sid, c.byref(sid_text)))
            for folder, rights in ((work, 'M'), (source, 'RX')):
                subprocess.run([acl_tool, str(folder), '/grant', f'*{sid_text.value}:(OI)(CI){rights}', '/T', '/Q'], check=True, capture_output=True, timeout=10, creationflags=0x08000000)
                acl_paths.append(folder)
            subprocess.run([acl_tool, str(work), '/setintegritylevel', '(OI)(CI)L', '/T', '/Q'], check=True, capture_output=True, timeout=10, creationflags=0x08000000)
            security = SecurityAttributes(c.sizeof(SecurityAttributes), None, True)
            output_handle = create_file(str(output), 0x40000000, 7, c.byref(security), 2, 0x80, None)
            input_handle = create_file('NUL', 0x80000000, 7, c.byref(security), 3, 0x80, None)
            if output_handle == ptr(-1).value or input_handle == ptr(-1).value:
                raise c.WinError(c.get_last_error())
            length = size()
            init_attributes(None, 2, 0, c.byref(length))
            attributes = c.create_string_buffer(length.value)
            check(init_attributes(attributes, 2, 0, c.byref(length)))
            capabilities = Capabilities(sid, None, 0, 0)
            handles = (w.HANDLE * 2)(input_handle, output_handle)
            check(update_attribute(attributes, 0, 0x20009, c.byref(capabilities), c.sizeof(capabilities), None, None))
            check(update_attribute(attributes, 0, 0x20002, handles, c.sizeof(handles), None, None))
            startup = StartupEx()
            startup.startup.cb, startup.startup.flags = c.sizeof(StartupEx), 0x100
            startup.startup.stdin = input_handle
            startup.startup.stdout = startup.startup.stderr = output_handle
            startup.attributes = c.cast(attributes, ptr)
            job = check(create_job(None, None))
            limits = ExtendedLimits()
            limits.basic.flags, limits.basic.processes = 0x2000 | 0x8 | 0x100, 1
            limits.process_memory = 256 * 1024 * 1024
            check(job_limits(job, 9, c.byref(limits), c.sizeof(limits)))
            env = {'SystemRoot': os.environ['SystemRoot'], 'WINDIR': os.environ['SystemRoot'], 'TEMP': str(work), 'TMP': str(work),
                   'USERPROFILE': str(work), 'LOCALAPPDATA': str(work), 'APPDATA': str(work)}
            environment = c.create_unicode_buffer('\0'.join(f'{key}={value}' for key, value in sorted(env.items())) + '\0\0')
            command = c.create_unicode_buffer(subprocess.list2cmdline([str(executable), *arguments]))
            check(create_process(str(executable), command, None, None, True, 0x80000 | 0x8000000 | 0x400 | 0x4, environment, str(work), c.byref(startup), c.byref(process)))
            check(assign_job(job, process.process))
            if resume(process.thread) == 0xffffffff:
                raise c.WinError(c.get_last_error())
            started, status = time.monotonic(), 'executed'
            while True:
                if cancelled():
                    status = 'cancelled'
                    break
                waited = wait(process.process, 50)
                if waited == 0:
                    break
                if waited != 258:
                    raise c.WinError(c.get_last_error())
                if time.monotonic() - started >= timeout:
                    status = 'timed_out'
                    break
                if output.stat().st_size > 256 * 1024:
                    status = 'output_limit'
                    break
            if status != 'executed':
                check(terminate_job(job, 1))
                wait(process.process, 5000)
            code = w.DWORD()
            check(exit_code(process.process, c.byref(code)))
            close(output_handle)
            output_handle = None
            with output.open('rb') as stream:
                captured = stream.read(256 * 1024).decode('utf-8', errors='replace')
            return {'status': status, 'exit_code': code.value if status == 'executed' else None,
                    'output': captured,
                    'isolation': 'windows-appcontainer-no-capabilities', 'duration_ms': round((time.monotonic() - started) * 1000)}
        finally:
            if process.process:
                terminate_process(process.process, 1)
                wait(process.process, 5000)
            for handle in (process.thread, process.process, job, input_handle, output_handle):
                if handle and handle != ptr(-1).value:
                    close(handle)
            if attributes:
                delete_attributes(attributes)
            cleanup_errors = []
            for folder in acl_paths:
                try:
                    subprocess.run([acl_tool, str(folder), '/remove:g', f'*{sid_text.value}', '/T', '/Q'], capture_output=True, timeout=10, check=True, creationflags=0x08000000)
                except (OSError, subprocess.SubprocessError) as error:
                    cleanup_errors.append(f'ACL cleanup failed for {folder}: {error}')
            if sid_text:
                local_free(c.cast(sid_text, ptr))
            if sid:
                free_sid(sid)
            if profile_created:
                result = delete_profile(name)
                if result < 0:
                    cleanup_errors.append(f'AppContainer cleanup failed for {name}: HRESULT {result}')
            if cleanup_errors:
                raise OSError('; '.join(cleanup_errors))
