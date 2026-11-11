from pathlib import Path
import unittest

from delivery_manager_toolbox.core.config import PROJECT_ROOT, load_settings


class ConfigTests(unittest.TestCase):
    def test_default_settings_are_loaded(self) -> None:
        settings = load_settings()

        self.assertEqual(settings.application.name, "Delivery Manager Toolbox")
        self.assertEqual(settings.logging.level, "INFO")
        self.assertTrue(settings.logging.file.is_absolute())
        self.assertEqual(settings.logging.file.parent, PROJECT_ROOT / "logs")
        self.assertEqual(settings.jira.people_file, PROJECT_ROOT / "config" / "jira_people.json")

    def test_explicit_config_path_is_supported(self) -> None:
        settings = load_settings(Path("config/settings.toml"))

        self.assertEqual(settings.application.subtitle, "Automatización modular para Delivery Management")


if __name__ == "__main__":
    unittest.main()

