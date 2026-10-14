"""Single-process storage; immutable materials/revisions, atomic project pointer."""

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from .models import SongState, SourceFile, empty_song, new_id
from .song_metadata import refresh_composition_metadata


class StorageError(Exception):
    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(UTC).isoformat()


class ProjectStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def directory(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", project_id):
            raise StorageError("歌曲不存在。", 404)
        path = self.root / project_id
        if path.is_symlink() or path.resolve() != path:
            raise StorageError("歌曲目录路径无效。", 400)
        return path

    def trash_directory(self, project_id: str | None = None) -> Path:
        path = self.root / ".trash"
        if path.is_symlink() or path.resolve() != path:
            raise StorageError("歌曲回收站路径无效。", 400)
        if project_id is not None:
            if not re.fullmatch(r"[a-f0-9]{32}", project_id):
                raise StorageError("已删除歌曲不存在。", 404)
            path /= project_id
            if path.is_symlink() or path.resolve() != path:
                raise StorageError("已删除歌曲目录路径无效。", 400)
        return path

    def write_bytes(self, path: Path, content: bytes):
        if not path.resolve().is_relative_to(self.root):
            raise StorageError("歌曲文件路径越界。", 400)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise StorageError("不允许向链接目录写入歌曲。", 400)
        descriptor, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def write_json(self, path: Path, content):
        self.write_bytes(
            path,
            json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
        )

    def read_json(self, path: Path):
        if not path.resolve().is_relative_to(self.root):
            raise StorageError("歌曲文件路径越界。", 400)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StorageError("歌曲或版本不存在。", 404) from None
        except (ValueError, UnicodeError):
            raise StorageError("歌曲记录损坏；未覆盖文件，请保留目录并检查备份。") from None

    def header(self, project_id: str):
        return self._header_at(self.directory(project_id), project_id)

    def _header_at(self, directory: Path, project_id: str):
        header = self.read_json(directory / "project.json")
        if not isinstance(header, dict) or header.get("id") != project_id:
            raise StorageError("歌曲记录格式损坏。")
        for key in (
            "current_revision",
            "revision_ids",
            "undo",
            "redo",
            "name",
            "created_at",
            "updated_at",
        ):
            if key not in header:
                raise StorageError("歌曲记录格式损坏。")
        try:
            if not all(
                isinstance(header[key], str)
                for key in ("name", "created_at", "updated_at", "current_revision")
            ):
                raise ValueError
            if not re.fullmatch(r"[a-f0-9]{32}", header["current_revision"]):
                raise ValueError
            ids = header["revision_ids"]
            if (
                not isinstance(ids, list)
                or not ids
                or any(
                    not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{32}", item)
                    for item in ids
                )
            ):
                raise ValueError
            if len(ids) != len(set(ids)) or header["current_revision"] not in ids:
                raise ValueError
            for key in ("undo", "redo"):
                if not isinstance(header[key], list) or any(
                    item not in ids for item in header[key]
                ):
                    raise ValueError
        except (TypeError, ValueError):
            raise StorageError("歌曲记录格式损坏；未修改原文件。") from None
        return header

    def revision(self, project_id: str, revision_id: str):
        return self._revision_at(self.directory(project_id), revision_id)

    def _revision_at(self, directory: Path, revision_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", revision_id):
            raise StorageError("版本不存在。", 404)
        result = self.read_json(directory / "revisions" / f"{revision_id}.json")
        try:
            if result["id"] != revision_id:
                raise ValueError
            SongState.model_validate(result["state"])
        except (KeyError, TypeError, ValueError):
            raise StorageError("歌曲版本损坏；未修改当前作品。") from None
        return result

    def envelope(self, header):
        return {
            **{
                key: header[key]
                for key in (
                    "id",
                    "name",
                    "created_at",
                    "updated_at",
                    "current_revision",
                )
            },
            "directory": f"data/{header['id']}",
            "can_undo": bool(header["undo"]),
            "can_redo": bool(header["redo"]),
            "revision": self.revision(header["id"], header["current_revision"]),
        }

    def get(self, project_id: str):
        with self.lock:
            return self.envelope(self.header(project_id))

    def list(self):
        items, warnings = [], []
        with self.lock:
            for directory in self.root.iterdir():
                if not directory.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", directory.name):
                    continue
                if not (directory / "project.json").exists():
                    continue  # An interrupted creation was never committed.
                try:
                    header = self.header(directory.name)
                    self.revision(directory.name, header["current_revision"])
                    items.append(
                        {
                            key: header[key]
                            for key in ("id", "name", "updated_at", "current_revision")
                        }
                    )
                except StorageError:
                    warnings.append(f"歌曲目录 {directory.name} 记录异常，已保留原文件。")
        return {
            "items": sorted(items, key=lambda item: item["updated_at"], reverse=True),
            "warnings": warnings,
        }

    def create(self, name: str):
        with self.lock:
            project_id = new_id()
            header = {
                "id": project_id,
                "name": name,
                "created_at": now(),
                "updated_at": now(),
                "current_revision": "",
                "revision_ids": [],
                "undo": [],
                "redo": [],
            }
            return self._commit(header, empty_song(), "新建空白歌曲")

    def delete(self, project_id: str, base_revision: str):
        """Move one complete song without rewriting any of its saved materials."""
        with self.lock:
            header = self.header(project_id)
            self.check_base(header, base_revision)
            source = self.directory(project_id)
            destination = self.trash_directory(project_id)
            if destination.exists():
                raise StorageError("回收站已有同一歌曲ID；未覆盖任何文件，请先检查回收站。", 409)
            destination.parent.mkdir(exist_ok=True)
            # Same-volume directory rename is the commit point; no recursive deletion.
            os.rename(source, destination)
            return {
                "id": project_id,
                "name": header["name"],
                "directory": f"data/.trash/{project_id}",
            }

    def list_trash(self):
        items, warnings = [], []
        with self.lock:
            root = self.trash_directory()
            if not root.exists():
                return {"items": [], "warnings": []}
            for directory in root.iterdir():
                if not re.fullmatch(r"[a-f0-9]{32}", directory.name):
                    continue
                try:
                    directory = self.trash_directory(directory.name)
                    header = self._header_at(directory, directory.name)
                    self._revision_at(directory, header["current_revision"])
                    items.append(
                        {
                            key: header[key]
                            for key in ("id", "name", "updated_at", "current_revision")
                        }
                    )
                except StorageError:
                    warnings.append(f"已删除歌曲 {directory.name} 记录异常，已保留原文件。")
        return {
            "items": sorted(items, key=lambda item: item["updated_at"], reverse=True),
            "warnings": warnings,
        }

    def restore_deleted(self, project_id: str):
        with self.lock:
            source = self.trash_directory(project_id)
            header = self._header_at(source, project_id)
            self._revision_at(source, header["current_revision"])
            destination = self.directory(project_id)
            if destination.exists():
                raise StorageError("歌曲列表已有同一歌曲ID，不能覆盖；回收站内容仍保留。", 409)
            os.rename(source, destination)
            return self.envelope(header)

    @staticmethod
    def check_base(header, base_revision: str):
        if header["current_revision"] != base_revision:
            raise StorageError("另一页面已修改这首歌。请先下载当前草稿，再重新打开最新版本。", 409)

    def _commit(self, header, state: SongState, label: str):
        state = refresh_composition_metadata(SongState.model_validate(state.model_dump()))
        directory, revision_id = self.directory(header["id"]), new_id()
        material = directory / "inputs" / revision_id
        self.write_json(material / "inputs.json", state.inputs.model_dump())
        self.write_bytes(material / "lyrics.txt", state.inputs.lyrics_text.encode("utf-8"))
        self.write_json(
            material / "performance.json",
            [note.model_dump() for note in state.inputs.recording],
        )
        revision = {
            "id": revision_id,
            "number": len(header["revision_ids"]) + 1,
            "created_at": now(),
            "label": label,
            "parent_revision": header["current_revision"] or None,
            "state": state.model_dump(),
        }
        self.write_json(directory / "revisions" / f"{revision_id}.json", revision)
        updated = {
            **header,
            "revision_ids": [*header["revision_ids"], revision_id],
            "undo": [*header["undo"], header["current_revision"]]
            if header["current_revision"]
            else [],
            "redo": [],
            "current_revision": revision_id,
            "updated_at": now(),
        }
        # Commit point. Failures before this retain the previous saved project.
        self.write_json(directory / "project.json", updated)
        return self.envelope(updated)

    def save(self, project_id: str, base_revision: str, state: SongState, label: str):
        with self.lock:
            header = self.header(project_id)
            self.check_base(header, base_revision)
            known = set()
            for revision_id in header["revision_ids"]:
                known.update(
                    file["id"]
                    for file in self.revision(project_id, revision_id)["state"]["inputs"]["files"]
                )
            if any(file.id not in known for file in state.inputs.files):
                raise StorageError("输入文件只能通过上传接口添加。", 422)
            return self._commit(header, state, label)

    def history(self, project_id: str):
        with self.lock:
            header = self.header(project_id)
            return [
                {
                    key: revision[key]
                    for key in (
                        "id",
                        "number",
                        "created_at",
                        "label",
                        "parent_revision",
                    )
                }
                for revision in (
                    self.revision(project_id, revision_id)
                    for revision_id in reversed(header["revision_ids"])
                )
            ]

    def move(self, project_id: str, base_revision: str, action: str):
        with self.lock:
            header = self.header(project_id)
            self.check_base(header, base_revision)
            source, destination = ("undo", "redo") if action == "undo" else ("redo", "undo")
            if not header[source]:
                raise StorageError("没有可撤销或重做的已保存版本。", 409)
            target = header[source][-1]
            self.revision(project_id, target)
            updated = {
                **header,
                source: header[source][:-1],
                destination: [*header[destination], header["current_revision"]],
                "current_revision": target,
                "updated_at": now(),
            }
            self.write_json(self.directory(project_id) / "project.json", updated)
            return self.envelope(updated)

    def restore(self, project_id: str, base_revision: str, revision_id: str):
        with self.lock:
            header = self.header(project_id)
            self.check_base(header, base_revision)
            if revision_id not in header["revision_ids"]:
                raise StorageError("该版本不属于当前歌曲。", 404)
            revision = self.revision(project_id, revision_id)
            return self._commit(
                header,
                SongState.model_validate(revision["state"]),
                f"恢复版本 v{revision['number']}",
            )

    def upload(
        self,
        project_id: str,
        base_revision: str,
        content: bytes,
        name: str,
        kind: str,
        transform,
    ):
        with self.lock:
            header = self.header(project_id)
            self.check_base(header, base_revision)
            state = SongState.model_validate(self.revision(project_id, base_revision)["state"])
            file = SourceFile(
                id=new_id(),
                name=name,
                kind=kind,
                sha256=hashlib.sha256(content).hexdigest(),
            )
            transform(state)
            state.inputs.files.append(file)
            state = SongState.model_validate(state.model_dump())
            extension = "txt" if kind == "lyrics" else "mid"
            self.write_bytes(
                self.directory(project_id) / "inputs" / "originals" / f"{file.id}.{extension}",
                content,
            )
            return self._commit(header, state, "上传歌词 TXT" if kind == "lyrics" else "导入 MIDI")

    def export(self, project_id: str, revision_id: str, content: bytes):
        with self.lock:
            header = self.header(project_id)
            if revision_id not in header["revision_ids"]:
                raise StorageError("版本不属于当前歌曲。", 404)
            self.write_bytes(self.directory(project_id) / "exports" / f"{revision_id}.mid", content)
