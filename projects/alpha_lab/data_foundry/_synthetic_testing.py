"""Test-only temporary filesystem harness for adversarial DABL plan tests.

It is intentionally private, temp-root-confined, sentinel-gated, and absent
from the public package surface. It must never be used for canonical state.
"""
from __future__ import annotations
import fcntl, os, stat, tempfile, uuid
from pathlib import Path
from typing import Callable, Sequence
from projects.alpha_lab.factory.errors import EventStoreIntegrityError
from .ledger import DABLEvent, plan_append, project_event_plan
from .models import strict_json_loads
from projects.alpha_lab.factory.canonical import canonical_json

_SENTINEL=".DABL_SYNTHETIC_TEST_ONLY_V1";_LOCK=".lock";_PREFIX="event-"
class SyntheticPreparationStore:
    """A test harness, not a production writer; only files beneath OS tmp work."""
    def __init__(self,root:Path,*,failure_injector:Callable[[str],None]|None=None):
        self.root=Path(root).resolve();self.tmp=Path(tempfile.gettempdir()).resolve();self.failure_injector=failure_injector
        try:self.root.relative_to(self.tmp)
        except ValueError as e:raise EventStoreIntegrityError("synthetic store must remain under OS temp") from e
    def initialize(self):
        self.root.mkdir(mode=0o700,parents=False,exist_ok=False)
        sentinel_created=False
        try:
            fd=os.open(str(self.root/_SENTINEL),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:
                if self.failure_injector:self.failure_injector("sentinel_write")
                self._write_all(fd,b"DABL synthetic test only\n");os.fsync(fd)
            finally:os.close(fd)
            sentinel_created=True
            fd=os.open(str(self.root/_LOCK),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:os.fsync(fd)
            finally:os.close(fd)
            dfd=os.open(str(self.root),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        except Exception:
            # This root was created by this invocation; clean only its known partial files.
            try:os.unlink(self.root/_SENTINEL)
            except FileNotFoundError:pass
            try:os.unlink(self.root/_LOCK)
            except FileNotFoundError:pass
            try:self.root.rmdir()
            except OSError:pass
            raise
    @staticmethod
    def _write_all(fd,data):
        offset=0
        while offset<len(data):
            written=os.write(fd,data[offset:])
            if not isinstance(written,int) or written<=0:raise EventStoreIntegrityError("synthetic short write")
            offset+=written
    def _dirfd(self):
        fd=os.open(str(self.root),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            directory=os.fstat(fd)
            if not stat.S_ISDIR(directory.st_mode) or stat.S_IMODE(directory.st_mode)!=0o700:raise EventStoreIntegrityError("synthetic directory mode invalid")
            s=os.stat(_SENTINEL,dir_fd=fd,follow_symlinks=False)
            if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or stat.S_IMODE(s.st_mode)!=0o600:raise EventStoreIntegrityError("synthetic sentinel metadata invalid")
            inp=os.open(_SENTINEL,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
            try:
                if os.read(inp,s.st_size)!=b"DABL synthetic test only\n":raise EventStoreIntegrityError("synthetic sentinel absent")
            finally:os.close(inp)
            l=os.stat(_LOCK,dir_fd=fd,follow_symlinks=False)
            if not stat.S_ISREG(l.st_mode) or l.st_nlink!=1 or stat.S_IMODE(l.st_mode)!=0o600:raise EventStoreIntegrityError("synthetic lock metadata invalid")
            return fd
        except Exception:
            os.close(fd);raise
    def _records(self,fd):
        result=[]
        names=sorted(name for name in os.listdir(fd) if name.startswith(_PREFIX) and name.endswith(".json"))
        for name in names:
            s=os.stat(name,dir_fd=fd,follow_symlinks=False)
            if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1:raise EventStoreIntegrityError("synthetic record must be regular single-link file")
            f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
            try:raw=os.read(f,s.st_size).decode("utf-8")
            finally:os.close(f)
            if not raw.endswith("\n"):raise EventStoreIntegrityError("partial synthetic record")
            result.append(DABLEvent.from_dict(strict_json_loads(raw)))
        project_event_plan(result);return tuple(result)
    def read_all(self):
        fd=self._dirfd()
        try:return self._records(fd)
        finally:os.close(fd)
    def append_for_test(self,*,expected_previous_head,event_id,event_type,occurred_at,payload):
        fd=self._dirfd()
        try:
            lock=os.open(_LOCK,os.O_RDWR|os.O_NOFOLLOW,dir_fd=fd)
            try:
                tmp=None
                try:
                    fcntl.flock(lock,fcntl.LOCK_EX);old=self._records(fd);new=plan_append(old,expected_previous_head=expected_previous_head,event_id=event_id,event_type=event_type,occurred_at=occurred_at,payload=payload)
                    if len(new)==len(old):return next(item for item in old if item.event_id==event_id)
                    event=new[-1]
                    if self.failure_injector:self.failure_injector("after_plan_before_write")
                    tmp=".pending-"+uuid.uuid4().hex
                    out=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                    try:
                        data=(canonical_json(event.to_dict())+"\n").encode();self._write_all(out,data)
                        os.fsync(out)
                    finally:os.close(out)
                    if self.failure_injector:self.failure_injector("after_write_before_publish")
                    target="{}{:020d}.json".format(_PREFIX,len(old)+1)
                    try:os.link(tmp,target,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
                    except FileExistsError as exc:raise EventStoreIntegrityError("synthetic publish collision") from exc
                    os.unlink(tmp,dir_fd=fd);tmp=None;os.fsync(fd)
                    return event
                except Exception:
                    if tmp is not None:
                        try:os.unlink(tmp,dir_fd=fd)
                        except FileNotFoundError:pass
                    raise
            finally:
                try:fcntl.flock(lock,fcntl.LOCK_UN)
                finally:os.close(lock)
        finally:os.close(fd)
