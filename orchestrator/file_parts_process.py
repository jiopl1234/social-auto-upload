"""Bounded local file fingerprints for browser-observed upload parts."""
import hashlib
import re
from pathlib import Path
import sys

from orchestrator.errors import TaskError
from orchestrator.preflight_process import child_main


MAX_SIZE = 32 * 1024 * 1024
MAX_PARTS = 64


def file_parts(request: dict) -> dict:
    path = Path(request["path"])
    before = path.stat()
    if not 0 < before.st_size <= MAX_SIZE:
        raise TaskError("UNSUPPORTED", "Video exceeds the verified preparation scope", field="media")
    observed = request.get("observed_parts")
    if observed is not None:
        if (not isinstance(observed,list) or not 1 <= len(observed) <= MAX_PARTS
                or any(not isinstance(part,dict) or set(part) != {"size","sha256","md5"}
                       or type(part["size"]) is not int or not 0 < part["size"] <= MAX_SIZE
                       or not isinstance(part["sha256"],str) or not re.fullmatch(r"[a-f0-9]{64}",part["sha256"])
                       or not isinstance(part["md5"],str) or not re.fullmatch(r"[a-f0-9]{32}",part["md5"])
                       for part in observed)
                or sum(part["size"] for part in observed) != before.st_size):
            raise TaskError("INVALID_INPUT", "Upload partition is invalid", field="media")
    whole = hashlib.sha256()
    whole_md5 = hashlib.md5()
    verified = True
    with path.open("rb") as stream:
        if observed is None:
            while data := stream.read(2 * 1024 * 1024):
                whole.update(data)
                whole_md5.update(data)
        else:
            # Verify the actual ordered partition, including a last part that
            # arrived first. Do not assume the frontend uses a fixed chunk size.
            for part in observed:
                data = stream.read(part["size"])
                whole.update(data)
                whole_md5.update(data)
                verified &= (len(data) == part["size"] and hashlib.sha256(data).hexdigest() == part["sha256"]
                             and hashlib.md5(data).hexdigest() == part["md5"])
    after = path.stat()
    if ((before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino)
            or whole.hexdigest() != request["sha256"]):
        raise TaskError("INVALID_INPUT", "Media changed before upload", field="media")
    result = {"size": before.st_size, "sha256": whole.hexdigest(), "md5": whole_md5.hexdigest()}
    if observed is not None:
        result["verified"] = verified
    return result


if __name__ == "__main__":
    sys.exit(child_main(file_parts) if sys.argv[1:] == ["--preflight-child"] else 2)
