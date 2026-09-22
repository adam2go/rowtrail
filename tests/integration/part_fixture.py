"""Corrupt a stored part without changing its descriptor or encoded length."""
import hashlib
import os
import pathlib


def corrupt_part(connection, result_ref):
    seq, path, checksum = connection.execute(
        'SELECT seq,path,checksum FROM parts WHERE result_id=? ORDER BY seq LIMIT 1',
        [result_ref]).fetchone()
    has_inline = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='inline_parts'").fetchone()
    inline = connection.execute(
        'SELECT data FROM inline_parts WHERE result_id=? AND seq=?',
        [result_ref, seq]).fetchone() if has_inline else None
    if inline:
        payload = bytearray(inline[0])
        assert hashlib.sha256(payload).hexdigest() == checksum
        payload[len(payload) // 2] ^= 1
        connection.execute('UPDATE inline_parts SET data=? WHERE result_id=? AND seq=?',
                           [bytes(payload), result_ref, seq])
        connection.commit()
    else:
        path = pathlib.Path(path)
        stat = path.stat()
        payload = bytearray(path.read_bytes())
        assert hashlib.sha256(payload).hexdigest() == checksum
        payload[len(payload) // 2] ^= 1
        path.write_bytes(payload)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
