"""PIE address reuse must not fabricate migration-success evidence."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class BKSProcessScopeTests(unittest.TestCase):
    def test_other_process_cannot_consume_budget_or_emit_success(self):
        path = Path(__file__).parents[1] / "re/bks_checkin_callbacks.py"
        spec = importlib.util.spec_from_file_location("bks_scope_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"lldb": types.ModuleType("lldb")}):
            spec.loader.exec_module(module)
        module.CONFIG[50] = {"label": "BKM_DM_RETURN"}
        module.SUCCESS_LABELS = {"BKM_DM_RETURN"}
        module.STOP_ON_SUCCESS = True
        frame, location = Mock(), Mock()
        location.GetBreakpoint.return_value.GetID.return_value = 50
        with patch.object(module, "_progname", return_value="fairplayd.H2"), \
                patch.object(module, "_write_event") as event:
            self.assertFalse(module.on_break(frame, location, {}))
        self.assertEqual(module.HITS, {})
        event.assert_not_called()
        frame.FindRegister.assert_not_called()


if __name__ == "__main__":
    unittest.main()
