"""Minimal stand-ins for the CMSSW Python config modules our cff files import.

Just enough of FWCore.ParameterSet.Config, PhysicsTools.NanoAOD.common_cff and
PhysicsTools.NanoAOD.muons_cff to load dimuonVertex_cff / dimuonNano_cff
outside CMSSW and inspect what they configure. Mirrors the CMSSW_13_0_X
definitions of Var, P3Vars/P4Vars and the muon gen-match modules.
"""
import builtins
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_PYTHON = os.path.join(os.path.dirname(HERE), "DiMuonTagger", "NanoProd", "python")

# Column types SimpleFlatTableProducer accepts (13_0 and 15_0).
ALLOWED_TYPES = {"int", "uint", "float", "double", "uint8", "int16", "uint16", "bool"}


class _Param(object):
    def __init__(self, value=None):
        self.value = value

    def __eq__(self, other):
        return isinstance(other, _Param) and type(self) is type(other) and self.value == other.value

    def __hash__(self):
        return hash((type(self).__name__, repr(self.value)))

    def __repr__(self):
        return "%s(%r)" % (type(self).__name__, self.value)


class string(_Param): pass
class bool(_Param): pass  # noqa: A001 (mirrors cms.bool)
class double(_Param): pass
class int32(_Param): pass
class uint32(_Param): pass


class vint32(_Param):
    def __init__(self, *values):
        super().__init__(list(values))


class InputTag(_Param):
    def __init__(self, *parts):
        super().__init__(":".join(parts))


class PSet(object):
    def __init__(self, *bases, **params):
        for base in bases:
            self.__dict__.update(base.parameters_())
        self.__dict__.update(params)

    def parameters_(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    def clone(self, **params):
        new = type(self).__new__(type(self))
        new.__dict__ = dict(self.__dict__)
        new.__dict__.update(params)
        return new


class _Module(PSet):
    def __init__(self, type_, *bases, **params):
        self._type = type_
        super().__init__(*bases, **params)

    def type_(self):
        return self._type


class EDProducer(_Module): pass
class EDFilter(_Module): pass
class ESProducer(_Module): pass


class Task(object):
    def __init__(self, *items):
        self.items = list(items)

    def add(self, *items):
        self.items.extend(items)


def _make_config_module():
    mod = types.ModuleType("FWCore.ParameterSet.Config")
    for name in ("string", "bool", "double", "int32", "uint32", "vint32", "InputTag",
                 "PSet", "EDProducer", "EDFilter", "ESProducer", "Task"):
        setattr(mod, name, globals()[name])
    return mod


def _make_common_cff(cms):
    mod = types.ModuleType("PhysicsTools.NanoAOD.common_cff")
    py_types = {int: "int", float: "float", builtins.bool: "bool"}

    def Var(expr, valtype, doc=None, precision=-1):
        return cms.PSet(expr=cms.string(expr), type=cms.string(py_types.get(valtype, valtype)),
                        doc=cms.string(doc if doc else expr), precision=cms.int32(precision))

    mod.Var = Var
    mod.P3Vars = cms.PSet(pt=Var("pt", float), phi=Var("phi", float, precision=12),
                          eta=Var("eta", float, precision=12))
    mod.P4Vars = cms.PSet(mod.P3Vars, mass=Var("mass", float, precision=10))
    return mod


def _make_muons_cff(cms):
    mod = types.ModuleType("PhysicsTools.NanoAOD.muons_cff")
    src = cms.InputTag("linkedObjects", "muons")
    mod.muonsMCMatchForTable = cms.EDProducer(
        "MCMatcher", src=src, matched=cms.InputTag("finalGenParticles"), mcPdgId=cms.vint32(13),
        checkCharge=cms.bool(False), mcStatus=cms.vint32(1), maxDeltaR=cms.double(0.3),
        maxDPtRel=cms.double(0.5), resolveAmbiguities=cms.bool(True), resolveByMatchQuality=cms.bool(True))
    mod.muonMCTable = cms.EDProducer(
        "CandMCMatchTableProducer", src=src, mcMap=cms.InputTag("muonsMCMatchForTable"),
        objName=cms.string("Muon"), objType=cms.string("Muon"), branchName=cms.string("genPart"),
        docString=cms.string("MC matching to status==1 muons"))
    return mod


def install():
    """Register the stub modules in sys.modules (idempotent); return the cms stub."""
    if "FWCore.ParameterSet.Config" in sys.modules:
        return sys.modules["FWCore.ParameterSet.Config"]
    cms = _make_config_module()
    for name in ("FWCore", "FWCore.ParameterSet", "PhysicsTools", "PhysicsTools.NanoAOD",
                 "DiMuonTagger", "DiMuonTagger.NanoProd"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["FWCore.ParameterSet.Config"] = cms
    sys.modules["PhysicsTools.NanoAOD.common_cff"] = _make_common_cff(cms)
    sys.modules["PhysicsTools.NanoAOD.muons_cff"] = _make_muons_cff(cms)
    for name in ("dimuonVertex_cff", "dimuonNano_cff"):
        full = "DiMuonTagger.NanoProd." + name
        spec = importlib.util.spec_from_file_location(full, os.path.join(PKG_PYTHON, name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
    return cms


class FakeProcess(object):
    """The parts of cms.Process that customiseDiMuonNano touches."""

    def __init__(self, cms):
        self.nanoTableTaskCommon = cms.Task()
        self.nanoTableTaskFS = cms.Task()
        self.genParticleTable = cms.EDProducer(
            "SimpleCandidateFlatTableProducer", src=cms.InputTag("finalGenParticles"),
            name=cms.string("GenPart"), variables=cms.PSet())
        self.loaded = []

    def load(self, name):
        self.loaded.append(name)
        module = sys.modules.get(name)
        if module is None:  # e.g. TransientTrackBuilder_cfi: just record it
            return
        for key, value in vars(module).items():
            if isinstance(value, (_Module, Task)):
                setattr(self, key, value)
