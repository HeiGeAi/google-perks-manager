"""Verify that the current Windows user controls the application's local files."""

from functools import lru_cache

import pywintypes
import win32api
import win32con
import win32event
import win32security


@lru_cache(maxsize=1)
def trusted_principals():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
    finally:
        token.Close()
    return (user, *(win32security.CreateWellKnownSid(kind, None)
                   for kind in (win32security.WinLocalSystemSid, win32security.WinBuiltinAdministratorsSid)))


def verify_access(path, private):
    trusted = trusted_principals()
    try:
        descriptor = win32security.GetNamedSecurityInfo(
            str(path), win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
        )
    except pywintypes.error:
        return False
    if descriptor.GetSecurityDescriptorOwner() not in trusted:
        return False
    if not private:
        return True
    acl = descriptor.GetSecurityDescriptorDacl()
    if acl is None:
        return False
    # Python's Windows mkdir(0700) grants full access to OWNER RIGHTS.
    # It is safe here because we already checked the owner of this object.
    owner_rights = win32security.ConvertStringSidToSid("S-1-3-4")
    for index in range(acl.GetAceCount()):
        header, *_rest, sid = acl.GetAce(index)
        if header[0] == win32security.ACCESS_DENIED_ACE_TYPE:
            continue
        if header[0] != win32security.ACCESS_ALLOWED_ACE_TYPE or (sid not in trusted and sid != owner_rights):
            return False
    return True


def ensure_process_running(pid):
    try:
        process = win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
        try:
            if win32event.WaitForSingleObject(process, 0) != win32event.WAIT_TIMEOUT:
                raise ProcessLookupError
        finally:
            process.Close()
    except pywintypes.error:
        raise ProcessLookupError from None
