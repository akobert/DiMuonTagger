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


def variables(table, external=True):
    out = table.variables.parameters_()
    if external and hasattr(table, "externalVariables"):
        out.update(table.externalVariables.parameters_())
    return out


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

    def test_external_variables_match_cpp_products(self):
        with open(os.path.join(PKG, "plugins", "DiMuonVertexProducer.cc")) as handle:
            source = handle.read()
        # produces<edm::ValueMap<T>>("label") -> {label: T}
        products = dict((label, ctype) for ctype, label in
                        re.findall(r'produces<edm::ValueMap<(\w+)>>\("(\w+)"\)', source))
        self.assertTrue(products)
        ext = dict(self.cff.displacedMuonTable.externalVariables.parameters_())
        ext.update(self.cff.muonDupTable.externalVariables.parameters_())
        self.assertEqual(len(ext), len(products))  # every ValueMap is stored, none is missing
        for name, var in ext.items():
            module, label = var.src.value.split(":")
            self.assertEqual(module, "diMuonVertices", name)
            self.assertIn(label, products, name)
            self.assertEqual(var.type.value, products[label], name)  # "int"/"float" read ValueMap<int/float>

    def test_muon_extension_table(self):
        table = self.cff.muonDupTable
        self.assertEqual(table.name.value, "Muon")
        self.assertTrue(table.extension.value)
        self.assertEqual(table.cut.value, "")  # rows must line up with the Muon table
        self.assertEqual(table.src, self.cff.diMuonVertices.muons)
        self.assertEqual(set(table.externalVariables.parameters_()), set(self.expected["Muon"]))
        self.assertEqual(table.variables.parameters_(), {})
        self.assertIn(table, self.cff.dimuonTablesTask.items)

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

    def test_unfiltered_keeps_every_event(self):
        process = cmssw_stub.FakeProcess(self.cms)
        self.customise(process)
        self.assertFalse(hasattr(process.NANOAODSIMoutput, "SelectEvents"))
        self.assertEqual(len(process.schedule), 3)


class TestCustomiseFiltered(unittest.TestCase):
    def setUp(self):
        self.cms = cmssw_stub.install()
        self.nano = sys.modules["DiMuonTagger.NanoProd.dimuonNano_cff"]

    def test_filter_selects_output_events(self):
        process = cmssw_stub.FakeProcess(self.cms)
        self.assertIs(self.nano.customiseDiMuonNanoFiltered(process), process)
        self.assertIn(process.dimuonTask, process.nanoTableTaskCommon.items)  # everything unfiltered does
        filt = process.diMuonVertexFilter
        self.assertEqual(filt.type_(), "CandViewCountFilter")
        self.assertEqual(filt.src.value, "diMuonVertices")  # the producer's label in dimuonVertex_cff
        self.assertIn("diMuonVertices", vars(sys.modules["DiMuonTagger.NanoProd.dimuonVertex_cff"]))
        self.assertEqual(filt.minNumber.value, 1)
        path = getattr(process, self.nano.FILTER_PATH)
        self.assertEqual(path.items, [filt])
        select = process.NANOAODSIMoutput.SelectEvents
        self.assertIsInstance(select, cmssw_stub.untracked.PSet)
        self.assertEqual(select.SelectEvents.value, [self.nano.FILTER_PATH])

    def test_gen_weights_run_on_every_event(self):
        process = cmssw_stub.FakeProcess(self.cms)
        self.nano.customiseDiMuonNanoFiltered(process)
        gen_path = getattr(process, self.nano.GEN_WEIGHTS_PATH)
        self.assertEqual(gen_path.items, [process.genWeightsTable])
        # both paths scheduled, ahead of the output step
        self.assertEqual(process.schedule[:2], [getattr(process, self.nano.FILTER_PATH), gen_path])
        self.assertIs(process.schedule[-1], process.NANOAODSIMoutput_step)

    def test_run2_variants(self):
        cff = sys.modules["DiMuonTagger.NanoProd.dimuonVertex_cff"]
        for name, filtered in (("customiseDiMuonNanoRun2", False), ("customiseDiMuonNanoRun2Filtered", True)):
            process = cmssw_stub.FakeProcess(self.cms)
            self.assertIs(getattr(self.nano, name)(process), process)
            selector = process.selectedDisplacedMuonsForNano
            self.assertEqual(selector.src.value, "slimmedMuons")   # exists in Run 2 MiniAOD
            self.assertEqual(selector.cut.value, "pt < 0")          # nothing passes: empty tables
            # the tables and the producer still read the (empty) selector
            self.assertEqual(process.displacedMuonTable.src.value, "selectedDisplacedMuonsForNano")
            self.assertEqual(process.diMuonVertices.displacedMuons.value, "selectedDisplacedMuonsForNano")
            self.assertIn(process.dimuonTablesTask, process.nanoTableTaskCommon.items)
            self.assertEqual(hasattr(process, "diMuonVertexFilter"), filtered, name)
        # the shared cff is untouched (Run 3 configs still read slimmedDisplacedMuons)
        self.assertEqual(cff.selectedDisplacedMuonsForNano.src.value, "slimmedDisplacedMuons")
        process = cmssw_stub.FakeProcess(self.cms)
        self.nano.customiseDiMuonNanoFiltered(process)
        self.assertEqual(process.selectedDisplacedMuonsForNano.src.value, "slimmedDisplacedMuons")

    def test_data_has_no_gen_weights_path(self):
        process = cmssw_stub.FakeProcess(self.cms, mc=False)
        self.nano.customiseDiMuonNanoFiltered(process)
        self.assertFalse(hasattr(process, self.nano.GEN_WEIGHTS_PATH))
        self.assertEqual(process.schedule[0], getattr(process, self.nano.FILTER_PATH))
        self.assertEqual(len(process.schedule), 4)


if __name__ == "__main__":
    unittest.main()
