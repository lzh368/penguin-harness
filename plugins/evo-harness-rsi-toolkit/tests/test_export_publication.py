"""Offline publication tests, including GDPevo's real tracked .keep layout."""
import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "skills/evo-harness-rsi-toolkit/scripts/evo.py"


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="evo-publication-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dest = self.root / "app/submission/skill"
        self.dest.mkdir(parents=True)
        self.marker = self.dest / ".keep"
        self.marker.write_bytes(b"")
        self.private = self.root / "logs/agent/evo-harness"
        self.private.mkdir(parents=True)
        helper = self.root / "state/skills/evo-harness-rsi-toolkit/scripts/evo.py"
        helper.parent.mkdir(parents=True)
        helper.write_bytes(TOOL.read_bytes())
        self.evo = module(helper, "publication_evo")
        self.training_path = helper.parent.parent / "training.json"
        self.training = {"guard_version": 2, "train_ids": [f"train_{i:03d}" for i in range(1, 6)],
                         "config_sha256": "a" * 64, "workspace": str(self.root / "app"),
                         "answer_dir": str(self.dest.parent / "answers"), "private_learning": str(self.private)}
        self.training_path.write_bytes(self.evo.encoded(self.training))
        self.library = self.private / "compiled.json"
        self.library.write_text("[]")
        self.req = {"output": str(self.dest), "library": str(self.library), "limits": {"general": 5, "topic": 5},
                    "max_entries": 5, "max_bytes": 16000}
        self.receipt = self.private / "skill-export-publication.json"

    def test_exact_placeholder_retained_with_source_receipt_and_no_package_changes(self):
        inode = self.marker.stat().st_ino
        exported = self.evo.export_skill(self.req)
        self.assertEqual(exported, self.evo.validate_package(self.dest))
        record = self.evo.read(self.receipt)
        self.assertEqual(record["status"], "published")
        self.assertEqual(record["skill_sha256"], exported["sha256"])
        self.assertEqual(record["training_sha256"], self.evo.digest(self.training_path.read_bytes()))
        self.assertIn("convert.py:write_training_run", record["source"])
        original = Path(record["backup"])
        self.assertEqual(record["placeholder"]["marker_inode"], inode)
        self.assertEqual(record["backup_kind"], "reserved_marker_file")
        self.assertTrue(record["backup_verified"])
        self.assertEqual(record["backup_placeholder"], self.evo.placeholder_marker(original))
        self.assertEqual(record["placeholder"]["marker_mode"], record["backup_placeholder"]["marker_mode"])
        self.assertEqual(self.evo.filemap(original), {".keep": self.evo.digest(b"")})
        self.assertFalse((self.dest / ".keep").exists())
        self.assertNotIn("skill-export-publication.json", exported["files"])
        before = self.evo.filemap(self.root)
        with self.assertRaises(ValueError):
            self.evo.export_skill(self.req)
        self.assertEqual(before, self.evo.filemap(self.root))

    def test_empty_directory_or_unknown_files_are_not_placeholders(self):
        self.marker.unlink()
        with self.assertRaisesRegex(ValueError, "empty .keep placeholder"):
            self.evo.export_skill(self.req)
        self.marker.write_bytes(b"")
        unknown = self.dest / "unknown"
        unknown.write_text("preserve")
        with self.assertRaisesRegex(ValueError, "empty .keep placeholder"):
            self.evo.export_skill(self.req)
        self.assertEqual(unknown.read_text(), "preserve")
        self.assertFalse(self.receipt.exists())

    def test_nonempty_linked_or_symbolic_marker_is_never_removed(self):
        self.marker.write_text("not a placeholder")
        with self.assertRaisesRegex(ValueError, "ordinary empty single-link"):
            self.evo.export_skill(self.req)
        self.assertEqual(self.marker.read_text(), "not a placeholder")
        self.marker.write_bytes(b"")
        link = self.private / "marker-link"
        os.link(self.marker, link)
        with self.assertRaisesRegex(ValueError, "ordinary empty single-link"):
            self.evo.export_skill(self.req)
        self.marker.unlink()
        self.marker.symlink_to(link)
        with self.assertRaises(OSError):
            self.evo.export_skill(self.req)
        self.assertTrue(self.marker.is_symlink())
        self.assertFalse(self.receipt.exists())

    def test_existing_destination_requires_exact_installed_training_binding(self):
        self.training["answer_dir"] = str(self.private / "answers")
        self.training_path.write_bytes(self.evo.encoded(self.training))
        with self.assertRaisesRegex(ValueError, "frozen GDPevo"):
            self.evo.export_skill(self.req)
        self.training_path.unlink()
        with self.assertRaisesRegex(ValueError, "no installed GDPevo"):
            self.evo.export_skill(self.req)
        self.assertEqual(self.marker.read_bytes(), b"")
        self.assertFalse(self.receipt.exists())

    def test_invalid_package_does_not_move_or_consume_placeholder(self):
        self.library.write_bytes(self.evo.encoded([{"id": "too-large", "level": "general", "trigger": "x",
            "scope": "x", "body": "X" * 16000}]))
        before = self.marker.stat().st_ino
        with self.assertRaisesRegex(ValueError, "entry cannot fit"):
            self.evo.export_skill(self.req)
        self.assertEqual(self.marker.stat().st_ino, before)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_cross_filesystem_private_logs_never_receive_a_directory_rename(self):
        rename = self.evo.rename_new
        calls = []
        def mount_checked(source, destination):
            calls.append((source, destination))
            if not all(Path(p).is_relative_to(self.dest.parent) for p in (source, destination)):
                raise OSError(errno.EXDEV, "fixture: logs and app are different filesystems")
            rename(source, destination)
        with patch.object(self.evo, "rename_new", side_effect=mount_checked):
            self.evo.export_skill(self.req)
        self.assertEqual(len(calls), 1)
        self.assertTrue(self.receipt.is_file())
        self.assertEqual(self.evo.read(self.receipt)["status"], "published")

    def test_real_distinct_filesystem_private_logs(self):
        try:
            temporary = tempfile.TemporaryDirectory(dir="/dev/shm", prefix="evo-publication-logs-")
        except OSError as error:
            self.skipTest("independent tmpfs fixture unavailable: " + type(error).__name__)
        self.addCleanup(temporary.cleanup)
        logs = Path(temporary.name)
        if logs.stat().st_dev == self.dest.stat().st_dev:
            self.skipTest("tmpfs is not a distinct filesystem")
        self.training["private_learning"] = str(logs)
        self.training_path.write_bytes(self.evo.encoded(self.training))
        library = logs / "compiled.json"
        library.write_bytes(self.library.read_bytes())
        exported = self.evo.export_skill({**self.req, "library": str(library)})
        receipt = self.evo.read(logs / "skill-export-publication.json")
        self.assertEqual(receipt["status"], "published")
        self.assertEqual(receipt["skill_sha256"], exported["sha256"])
        self.assertNotEqual(logs.stat().st_dev, self.dest.stat().st_dev)
        self.assertEqual(Path(receipt["backup"]).stat().st_dev, self.dest.stat().st_dev)

    def test_symlinked_submission_parent_is_rejected(self):
        original = self.dest.parent
        renamed = original.with_name("original-submission")
        original.rename(renamed)
        original.symlink_to(renamed, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "canonical without symlinks"):
            self.evo.export_skill(self.req)
        self.assertEqual((renamed / "skill/.keep").read_bytes(), b"")
        self.assertFalse(self.receipt.exists())

    def test_failed_publish_restores_placeholder_without_deleting_evidence(self):
        inode = self.marker.stat().st_ino
        rename = self.evo.rename_new
        def fail_publish(source, destination):
            if Path(source).name == "skill" and Path(source) != self.dest:
                raise PermissionError("synthetic publication failure")
            rename(source, destination)
        with patch.object(self.evo, "rename_new", side_effect=fail_publish):
            with self.assertRaisesRegex(PermissionError, "publication failure"):
                self.evo.export_skill(self.req)
        self.assertEqual(self.marker.read_bytes(), b"")
        receipt = self.evo.read(self.receipt)
        self.assertEqual(receipt["placeholder"]["marker_inode"], inode)
        self.assertTrue((Path(receipt["backup"]) / ".keep").is_file())
        self.assertEqual(receipt["restored_placeholder"], self.evo.placeholder_marker(self.dest))
        self.assertTrue(receipt["placeholder_restored"])
        self.assertFalse(receipt["skill_published"])
        with self.assertRaisesRegex(ValueError, "prior export publication"):
            self.evo.export_skill(self.req)

    def test_publication_and_rollback_never_replace_a_concurrent_writer(self):
        rename = self.evo.rename_new
        def racing_writer(source, destination):
            if Path(source).name == "skill" and Path(source) != self.dest:
                self.dest.mkdir()
                (self.dest / "foreign.txt").write_text("preserve racing writer")
            rename(source, destination)
        with patch.object(self.evo, "rename_new", side_effect=racing_writer):
            with self.assertRaises(FileExistsError):
                self.evo.export_skill(self.req)
        receipt = self.evo.read(self.receipt)
        self.assertFalse(receipt["placeholder_restored"])
        self.assertEqual((self.dest / "foreign.txt").read_text(), "preserve racing writer")
        self.assertEqual((Path(receipt["backup"]) / ".keep").read_bytes(), b"")

    def test_failed_publish_and_failed_rollback_cannot_bypass_receipt_when_dest_missing(self):
        def fail_after_backup(source, destination):
            raise PermissionError("synthetic publication and rollback failure")
        with patch.object(self.evo, "rename_new", side_effect=fail_after_backup):
            with self.assertRaisesRegex(PermissionError, "rollback failure"):
                self.evo.export_skill(self.req)
        self.assertFalse(self.dest.exists())
        receipt = self.evo.read(self.receipt)
        self.assertFalse(receipt["placeholder_restored"])
        backup = Path(receipt["backup"])
        before = self.evo.filemap(backup)
        with self.assertRaisesRegex(ValueError, "prior export publication"):
            self.evo.export_skill(self.req)
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.evo.filemap(backup), before)
        self.assertEqual(self.evo.read(self.receipt), receipt)

    def test_overlay_lower_directory_exdev_is_never_triggered(self):
        """Image-layer directory rename fails despite matching st_dev; new upper stage does not."""
        rename = self.evo.rename_new
        calls = []
        def overlay_rename(source, destination):
            calls.append((Path(source), Path(destination)))
            if Path(source) == self.dest:
                raise OSError(errno.EXDEV, "overlay lower directory cannot be renamed", str(destination))
            rename(source, destination)
        with patch.object(self.evo, "rename_new", side_effect=overlay_rename):
            exported = self.evo.export_skill(self.req)
        self.assertEqual(exported, self.evo.validate_package(self.dest))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], self.dest)
        self.assertNotEqual(calls[0][0], self.dest)
        receipt = self.evo.read(self.receipt)
        self.assertTrue(receipt["marker_removed"])
        self.assertTrue(receipt["placeholder_removed"])
        self.assertEqual(receipt["schema"], "evo-gdpevo-placeholder-publication-v2")

    def test_marker_reservation_failure_preserves_original_and_reserved_receipt(self):
        identity = self.evo.placeholder_marker(self.dest)
        rename_at = self.evo.rename_new_at
        def blocked_marker(source_fd, source, destination_fd, destination):
            if source == ".keep":
                raise PermissionError("synthetic marker reservation denied")
            return rename_at(source_fd, source, destination_fd, destination)
        with patch.object(self.evo, "rename_new_at", side_effect=blocked_marker):
            with self.assertRaisesRegex(PermissionError, "reservation denied"):
                self.evo.export_skill(self.req)
        self.assertEqual(self.evo.placeholder_marker(self.dest), identity)
        receipt = self.evo.read(self.receipt)
        self.assertFalse(receipt["marker_removed"])
        self.assertFalse(receipt["placeholder_removed"])
        self.assertEqual(list(Path(receipt["backup"]).iterdir()), [])
        with self.assertRaisesRegex(ValueError, "prior export publication"):
            self.evo.export_skill(self.req)

    def test_rmdir_failure_restores_marker_without_renaming_lower_directory(self):
        self.marker.chmod(0o640)
        identity = self.evo.placeholder_marker(self.dest)
        rmdir = self.evo.os.rmdir
        def blocked_slot(path, **kwargs):
            if Path(path) == self.dest:
                raise OSError(errno.EBUSY, "synthetic occupied mount")
            return rmdir(path, **kwargs)
        with patch.object(self.evo.os, "rmdir", side_effect=blocked_slot), \
             patch.object(self.evo, "rename_new", side_effect=AssertionError("no lower-layer rename allowed")):
            with self.assertRaisesRegex(OSError, "occupied mount"):
                self.evo.export_skill(self.req)
        current = self.evo.placeholder_marker(self.dest)
        self.assertEqual(current["directory_inode"], identity["directory_inode"])
        self.assertEqual(current["marker_mode"], identity["marker_mode"])
        receipt = self.evo.read(self.receipt)
        self.assertTrue(receipt["marker_removed"])
        self.assertFalse(receipt["placeholder_removed"])
        self.assertTrue(receipt["placeholder_restored"])

    def test_concurrent_file_during_rmdir_is_preserved_without_recursive_cleanup(self):
        rmdir = self.evo.os.rmdir
        def add_foreign(path, **kwargs):
            if Path(path) == self.dest:
                (self.dest / "foreign.txt").write_text("preserve concurrent data")
            return rmdir(path, **kwargs)
        with patch.object(self.evo.os, "rmdir", side_effect=add_foreign):
            with self.assertRaises(OSError):
                self.evo.export_skill(self.req)
        self.assertEqual((self.dest / "foreign.txt").read_text(), "preserve concurrent data")
        receipt = self.evo.read(self.receipt)
        self.assertFalse(receipt["placeholder_removed"])
        self.assertFalse(receipt["placeholder_restored"])
        self.assertEqual((Path(receipt["backup"]) / ".keep").read_bytes(), b"")

    def test_changed_marker_after_backup_is_not_unlinked(self):
        write_once = self.evo.write_once
        def changed_source(destination, data):
            result = write_once(destination, data)
            if Path(destination) == self.receipt:
                self.marker.write_text("new data must survive")
            return result
        with patch.object(self.evo, "write_once", side_effect=changed_source):
            with self.assertRaises(ValueError):
                self.evo.export_skill(self.req)
        self.assertEqual(self.marker.read_text(), "new data must survive")
        receipt = self.evo.read(self.receipt)
        self.assertFalse(receipt["marker_removed"])
        self.assertEqual(list(Path(receipt["backup"]).iterdir()), [])

    def test_late_marker_write_is_preserved_as_actual_file_and_never_published(self):
        rename_at = self.evo.rename_new_at
        def late_write(source_fd, source, destination_fd, destination):
            if source == ".keep":
                self.marker.write_text("concurrent content must survive")
            return rename_at(source_fd, source, destination_fd, destination)
        with patch.object(self.evo, "rename_new_at", side_effect=late_write):
            with self.assertRaisesRegex(ValueError, "ordinary empty single-link"):
                self.evo.export_skill(self.req)
        receipt = self.evo.read(self.receipt)
        self.assertEqual(receipt["status"], "publication_failed")
        self.assertFalse(receipt["backup_verified"])
        self.assertFalse(receipt["placeholder_removed"])
        self.assertFalse(receipt["skill_published"])
        self.assertEqual((Path(receipt["backup"]) / ".keep").read_text(), "concurrent content must survive")
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_late_empty_marker_replacement_is_retained_and_identity_change_rejected(self):
        rename_at = self.evo.rename_new_at
        replacement = {}
        def late_replace(source_fd, source, destination_fd, destination):
            if source != ".keep":
                return rename_at(source_fd, source, destination_fd, destination)
            held = os.open(".keep", os.O_RDONLY, dir_fd=source_fd)
            try:
                os.unlink(".keep", dir_fd=source_fd)
                self.marker.write_bytes(b"")
                replacement["inode"] = self.marker.stat().st_ino
                self.assertNotEqual(replacement["inode"], os.fstat(held).st_ino)
                return rename_at(source_fd, source, destination_fd, destination)
            finally:
                os.close(held)
        with patch.object(self.evo, "rename_new_at", side_effect=late_replace):
            with self.assertRaisesRegex(ValueError, "marker changed while being reserved"):
                self.evo.export_skill(self.req)
        receipt = self.evo.read(self.receipt)
        self.assertFalse(receipt["backup_verified"])
        self.assertFalse(receipt["placeholder_removed"])
        backup_marker = Path(receipt["backup"]) / ".keep"
        self.assertEqual(backup_marker.stat().st_ino, replacement["inode"])
        self.assertEqual(backup_marker.read_bytes(), b"")

    def test_generic_workspace_export_does_not_require_linux_libc(self):
        fresh = self.root / "generic-workspace/learned"
        with patch.object(self.evo.sys, "platform", "darwin"), \
             patch.object(self.evo.ctypes, "CDLL", side_effect=AssertionError("Linux API must not be required")):
            package = self.evo.export_skill({**self.req, "output": str(fresh)})
        self.assertEqual(package, self.evo.validate_package(fresh))
        self.assertFalse(self.receipt.exists())

    def test_new_destination_atomic_publish_never_overwrites_even_empty_directory(self):
        src, dst = self.root / "new-stage", self.root / "occupied"
        src.mkdir()
        (src / "file").write_text("stage")
        dst.mkdir()
        with self.assertRaises(FileExistsError):
            self.evo.rename_new(src, dst)
        self.assertEqual((src / "file").read_text(), "stage")
        self.assertEqual(list(dst.iterdir()), [])


@unittest.skipUnless(os.environ.get("EVO_BENCHMARK"), "set EVO_BENCHMARK for full public GDPevo protocol integration")
class GDPevoPublicationChainTests(unittest.TestCase):
    def test_real_keep_compile_export_seal_verify_and_relocation(self):
        repo = Path(os.environ["EVO_BENCHMARK"]).resolve()
        fixture_dir = repo / "experiments/gdpevo/evo-harness/v1"
        with patch.object(sys, "path", [str(fixture_dir), *sys.path]):
            fixtures = module(fixture_dir / "test_protocol.py", "publication_protocol_fixtures")
        original = repo / "benchmarks/gdpevo/tasks/task_group_003/tg003-train/environment/workspace/submission/skill/.keep"
        self.assertEqual(original.read_bytes(), b"")

        class WithOriginalPlaceholder(fixtures.ExportEvidenceTests):
            @staticmethod
            def save(path, value):
                result = fixtures.ExportEvidenceTests.save(path, value)
                # Reproduce the converter-created public directory before the
                # first training answer. All compiler/export/seal calls stay real.
                if Path(path).parent.name == "answers":
                    skill = Path(path).parent.parent / "skill"
                    if not skill.exists():
                        skill.mkdir()
                        shutil.copyfile(original, skill / ".keep")
                return result

        fixture = WithOriginalPlaceholder(methodName="runTest")
        with patch.dict(os.environ, EVO_TOOL=str(TOOL)):
            fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        receipt = fixture.evo.read(fixture.private / "skill-export-publication.json")
        self.assertEqual(receipt["status"], "published")
        self.assertEqual(fixture.package, fixture.evo.validate_package(fixture.submission / "skill"))
        sealed = fixture.evo.seal_training(fixture.seal_request)
        self.assertTrue(sealed["complete"])
        self.assertEqual(fixture.verify(), fixture.package)
        moved = fixture.root / "exported-harbor-artifacts"
        shutil.move(fixture.run, moved)
        self.assertEqual(fixture.verify(moved), fixture.package)
        self.assertTrue((moved / "private/skill-export-publication.json").is_file())


if __name__ == "__main__":
    unittest.main()
