"""Offline checks of the custom-NanoAOD Python config and its C++ counterpart."""
import ast
import os
import re
import sys
import unittest

import cmssw_stub

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "DiMuonTagger", "NanoProd")


def expected_branches():
    """EXPECTED from check_nano.py, read without importing uproot/awkward."""
    with open(os.path.join(PKG, "test", "check_nano.py")) as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "EXPECTED" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("EXPECTED not found in check_nano.py")


def variables(table):
    return table.variables.parameters_()


class TestDimuonVertexCff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cms = cmssw_stub.install()
        cls.cff = sys.modules["DiMuonTagger.NanoProd.dimuonVertex_cff"]
        cls.expected = expected_branches()

    def test_dimuon_table_matches_spec(self):
        self.assertEqual(set(variables(self.cff.diMuonVertexTable)), set(self.expected["DiMuonVtx"]))
        self.assertEqual(self.cff.diMuonVertexTable.name.value, "DiMuonVtx")

    def test_displaced_muon_table_matches_spec(self):
        mc_branches = {"genPartIdx", "genPartFlav"}  # added by the CandMCMatchTableProducer
        self.assertEqual(set(variables(self.cff.displacedMuonTable)) | mc_branches,
                         set(self.expected["DisplacedMuon"]))
        self.assertEqual(self.cff.displacedMuonMCTable.branchName.value, "genPart")

    def test_column_types_are_supported(self):
        for table in (self.cff.diMuonVertexTable, self.cff.displacedMuonTable):
            for name, var in variables(table).items():
                self.assertIn(var.type.value, cmssw_stub.ALLOWED_TYPES, name)
                self.assertNotIn("lazyEval", var.parameters_(), name)

    def test_tables_are_kept_by_nanoaod_output(self):
        # NanoAOD output keeps nanoaodFlatTable_*Table_*_*
        tasks = (self.cff.dimuonTask, self.cff.dimuonTablesTask, self.cff.dimuonMCTask)
        labels = {id(v): k for k, v in vars(self.cff).items() if isinstance(v, cmssw_stub._Module)}
        for task in tasks:
            for module in task.items:
                if "Table" in module.type_():
                    self.assertTrue(labels[id(module)].endswith("Table"), labels[id(module)])

    def test_indices_line_up_with_tables(self):
        cff = self.cff
        # producer inputs must be exactly the table sources (tables have no cut)
        self.assertEqual(cff.diMuonVertices.displacedMuons, cff.displacedMuonTable.src)
        self.assertEqual(cff.diMuonVertices.muons.value, "linkedObjects:muons")
        self.assertEqual(cff.displacedMuonTable.cut.value, "")
        self.assertEqual(cff.diMuonVertexTable.cut.value, "")
        # gen match targets the DisplacedMuon table, from the same source
        self.assertEqual(cff.displacedMuonMCTable.objName, cff.displacedMuonTable.name)
        self.assertEqual(cff.displacedMuonMCTable.src, cff.displacedMuonTable.src)
        self.assertEqual(cff.displacedMuonsMCMatchForTable.src, cff.displacedMuonTable.src)
        self.assertEqual(cff.displacedMuonMCTable.mcMap.value, "displacedMuonsMCMatchForTable")

    def test_user_fields_exist_in_cpp(self):
        with open(os.path.join(PKG, "plugins", "DiMuonVertexProducer.cc")) as handle:
            source = handle.read()
        produced = set(re.findall(r'addUser(?:Float|Int)\("(\w+)"', source))
        used = set()
        for var in variables(self.cff.diMuonVertexTable).values():
            used |= set(re.findall(r"user(?:Float|Int)\('(\w+)'\)", var.expr.value))
        self.assertTrue(used, "no userFloat/userInt expressions found")
        self.assertEqual(used - produced, set(), "table reads fields the producer never sets")
        self.assertEqual(produced - used, set(), "producer sets fields the table never stores")

    def test_cpp_parameters_match_config(self):
        with open(os.path.join(PKG, "plugins", "DiMuonVertexProducer.cc")) as handle:
            source = handle.read()
        described = set(re.findall(r'desc\.add<[^>]+>\("(\w+)"', source))
        configured = set(self.cff.diMuonVertices.parameters_())
        self.assertEqual(configured, described)


class TestCustomise(unittest.TestCase):
    def setUp(self):
        self.cms = cmssw_stub.install()
        self.customise = sys.modules["DiMuonTagger.NanoProd.dimuonNano_cff"].customiseDiMuonNano

    def test_customise_adds_tasks_and_gen_vertex(self):
        process = cmssw_stub.FakeProcess(self.cms)
        self.assertIs(self.customise(process), process)
        self.assertIn("TrackingTools.TransientTrack.TransientTrackBuilder_cfi", process.loaded)
        self.assertIn("DiMuonTagger.NanoProd.dimuonVertex_cff", process.loaded)
        self.assertIn(process.dimuonTask, process.nanoTableTaskCommon.items)
        self.assertIn(process.dimuonTablesTask, process.nanoTableTaskCommon.items)
        self.assertIn(process.dimuonMCTask, process.nanoTableTaskFS.items)
        self.assertNotIn(process.dimuonMCTask, process.nanoTableTaskCommon.items)
        gen_vars = process.genParticleTable.variables.parameters_()
        expected = expected_branches()["GenPart"]
        for coord in expected:
            self.assertEqual(gen_vars[coord].expr.value, coord)
            self.assertEqual(gen_vars[coord].precision.value, -1)


if __name__ == "__main__":
    unittest.main()
