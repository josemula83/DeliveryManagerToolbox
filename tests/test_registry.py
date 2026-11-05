import unittest

from delivery_manager_toolbox.core.registry import discover_modules


class RegistryTests(unittest.TestCase):
    def test_expected_modules_are_discovered_in_order(self) -> None:
        modules = discover_modules()

        self.assertEqual(
            [module.key for module in modules],
            ["people_360", "jira", "activity_report", "facturacion"],
        )
        self.assertTrue(all(callable(module.render) for module in modules))


if __name__ == "__main__":
    unittest.main()

