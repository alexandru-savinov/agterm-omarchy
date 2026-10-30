import importlib.util, os, shutil, sys, unittest
from pathlib import Path
source=Path(sys.argv.pop(1)).resolve()
spec=importlib.util.spec_from_file_location('installer_fixtures',source/'scripts/tests/test_linux_install.py')
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
class LinkTests(fixtures.PersonalInstallTests):
    def test_symlinked_bin_resolves_both_launchers(self):
        real=self.work/'actual bin';real.mkdir();self.prefix.mkdir();(self.prefix/'bin').symlink_to(real,target_is_directory=True)
        self.install('--no-build')
        for name in ('agterm-linux','agtermctl'):
            link=self.prefix/'bin'/name
            self.assertTrue(link.exists(),str(link))
            self.assertEqual(link.resolve(),self.prefix/'lib/agterm/bin'/name)
        self.install('--no-build')
        self.assertTrue((self.prefix/'bin/agtermctl').exists())
    def test_ordinary_prefix_remains_relocatable(self):
        self.install('--no-build'); moved=self.work/'moved prefix';self.prefix.rename(moved)
        for name in ('agterm-linux','agtermctl'):
            link=moved/'bin'/name
            self.assertFalse(Path(os.readlink(link)).is_absolute())
            self.assertEqual(link.resolve(),moved/'lib/agterm/bin'/name)
            self.assertTrue(link.exists())
    def test_symlinked_prefix_and_bin_resolve(self):
        realprefix=self.work/'real prefix';realprefix.mkdir();self.prefix.symlink_to(realprefix,target_is_directory=True)
        realbin=self.work/'real bin';realbin.mkdir();(realprefix/'bin').symlink_to(realbin,target_is_directory=True)
        self.install('--no-build')
        self.assertTrue((self.prefix/'bin/agtermctl').exists())
        self.assertEqual((self.prefix/'bin/agtermctl').resolve(),realprefix/'lib/agterm/bin/agtermctl')
unittest.main()
