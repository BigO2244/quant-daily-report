"""Adversarial tests for the Phase-1-blocked, pure DABL preparation surface."""
from __future__ import annotations
import datetime as dt
import json, threading
from dataclasses import replace
from pathlib import Path
import pytest
import projects.alpha_lab.data_foundry as dabl
from projects.alpha_lab.data_foundry._synthetic_testing import SyntheticPreparationStore
from projects.alpha_lab.factory.canonical import canonical_hash
from projects.alpha_lab.factory.errors import ContractValidationError, EventStoreIntegrityError

H="a"*64; NOW=dt.datetime(2026,8,23,12,tzinfo=dt.timezone.utc)
def cjson(value): return __import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_json"]).canonical_json(value)
def bsha(value): return __import__("hashlib").sha256(value.encode("utf-8")).hexdigest()
def receipt(): return dabl.SignedExportBindingReceipt("caerus_alpha_lab_signed_projection_export_v1","SIGNED_RESEARCH_ONLY_NON_EXECUTIONAL",NOW,"projects/alpha_lab/ledger/events.jsonl","caerus_alpha_lab_source_ledger_receipt_v1",H,1,H,{"0":None,"1":H},NOW,True,True,H,True,H,H,H,H,H,H,H)
def ref(): return dabl.LedgerReference("FAM-2026-001","HYP-2026-001","EXP-2026-0001","CAND-2026-001",receipt())
def route(kind=dabl.SourceRouteKind.OWNED_FREE): return dabl.SourceRoute("SRT-2026-001","SRC-2026-001",kind,"synthetic","synthetic", "owner", "exact synthetic scope",False,kind in {dabl.SourceRouteKind.PAID,dabl.SourceRouteKind.LICENSED_TRIAL},False,kind in {dabl.SourceRouteKind.PAID,dabl.SourceRouteKind.LICENSED_TRIAL},kind in {dabl.SourceRouteKind.PAID,dabl.SourceRouteKind.LICENSED_TRIAL},kind in {dabl.SourceRouteKind.PAID,dabl.SourceRouteKind.LICENSED_TRIAL},0.0,1.0,"exact synthetic equivalence","cannot make alpha/lifecycle claim")
def definition(): return dabl.DataAssetDefinition("DAD-2026-001","synthetic",dabl.AssetClass.RAW,"source","claim","SRC-2026-001","synthetic","synthetic","owner",ref(),(route(),),("LANE-HYP-2026-001",))
def license(): return dabl.LicenseTerms("LIC-2026-001","synthetic","synthetic",H,True,"retain synthetic","no redistribute",None)
def review(): return dabl.SourceRouteLicenseReview("LRV-2026-001","SRT-2026-001","SRC-2026-001","synthetic","synthetic","exact synthetic scope",__import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_hash"]).canonical_hash(route().to_dict()),"LIC-2026-001","license-reviewer",NOW,H)
def facts(status="EVIDENCED"):
    return {k:{"status":status,"evidence_sha256":H if status=="EVIDENCED" else None,"reason":None if status=="EVIDENCED" else "not applicable to this synthetic field","observed_at":"2026-08-23T12:00:00Z"} for k in ("license","pit","universe","delisting","corporate_action","revision","missingness","freshness")}
def version(): return dabl.DataAssetVersion("DAV-2026-001-abcdef123456","DAD-2026-001","SRC-2026-001","SRT-2026-001",H,H,H,{"upstream":H},dabl.InputImmutability.CREATE_ONLY_IMMUTABLE,license(),H,H,H,NOW,NOW,NOW,NOW,NOW,facts(),("LANE-HYP-2026-001",))

def append(records,event_id,event_type,payload,at=NOW): return dabl.plan_append(records,expected_previous_head=records[-1].event_hash if records else None,event_id=event_id,event_type=event_type,occurred_at=at,payload=payload)[-1]
def valid_records():
    xs=()
    def add(eid,typ,payload):
        nonlocal xs
        xs=dabl.plan_append(xs,expected_previous_head=xs[-1].event_hash if xs else None,event_id=eid,event_type=typ,occurred_at=NOW,payload=payload.to_dict())
    add("DABL-EVT-2026-000001","source_registration",dabl.SourceRegistration("SRC-2026-001","synthetic","synthetic","owner",H,NOW))
    add("DABL-EVT-2026-000002","license_terms",license())
    add("DABL-EVT-2026-000003","asset_definition",definition())
    add("DABL-EVT-2026-000004","route_license_review",review())
    add("DABL-EVT-2026-000005","asset_version",version())
    return xs

def test_public_surface_has_no_persistent_writer_and_all_draft_permissions_false():
    assert not hasattr(dabl,"DABLEventStore")
    with pytest.raises(ImportError):exec("from projects.alpha_lab.data_foundry import DABLEventStore",{})
    xs=valid_records(); p=dabl.project_event_plan(xs)
    assert p.authority_state is dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED
    assert p.frozen_evaluator_permitted is p.alpha_or_lifecycle_claim_permitted is False
    packet=dabl.DataReadinessPacket("DRP-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,ref(),("DAD-2026-001",),("DCE-2026-001",),dabl.DataTier.A,dabl.NextAction.PREPARE_OWNER_REVIEW,NOW)
    assert packet.frozen_evaluator_permitted is packet.alpha_or_lifecycle_claim_permitted is False
    assert ref().externally_authenticated is False

@pytest.mark.parametrize("raw",['{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}','{"x":-Infinity}'])
def test_strict_json_rejects_duplicate_and_nonfinite(raw):
    with pytest.raises(ContractValidationError):dabl.strict_json_loads(raw)

def test_deep_freeze_schema_manifest_and_exact_timestamp_roundtrip():
    with pytest.raises(TypeError): dabl.SCHEMA_MANIFEST_V2["x"]=1
    with pytest.raises(ContractValidationError): dabl.parse_utc("2026-08-23T12:00:00.000Z","x")
    assert dabl.parse_utc("2026-08-23T12:00:00Z","x")==NOW
    raw=version().to_dict(); raw["facts"]["pit"]["reason"]="mutated"; assert version().facts["pit"]["reason"] is None
    empty=dabl.SignedExportBindingReceipt("caerus_alpha_lab_signed_projection_export_v1","SIGNED_RESEARCH_ONLY_NON_EXECUTIONAL",NOW,"projects/alpha_lab/ledger/events.jsonl","caerus_alpha_lab_source_ledger_receipt_v1",H,0,None,{"0":None},None,True,True,H,True,H,H,H,H,H,H,H)
    assert empty.head_by_event_count["0"] is None

@pytest.mark.parametrize("mutator",[
    lambda m:m.__setitem__("dabl_event_schema_version","changed"),
    lambda m:m["contract_field_metadata"]["DataAssetDefinition"][0].__setitem__("name","changed"),
    lambda m:m["contract_field_metadata"]["DataAssetDefinition"][0].__setitem__("type","changed"),
    lambda m:m["contract_field_metadata"]["DataAssetDefinition"][0].__setitem__("required",False),
    lambda m:m["contract_field_metadata"]["DataAssetDefinition"][0].__setitem__("default","changed"),
    lambda m:m["fact_keys"].__setitem__(0,"changed"),
    lambda m:m["dabl_event_fields"].__setitem__(0,"changed"),
    lambda m:m["dabl_event_field_metadata"][0].__setitem__("type","changed"),
    lambda m:m["dabl_event_field_metadata"][1].__setitem__("required",False),
    lambda m:m["dabl_event_field_metadata"][2].__setitem__("default","changed"),
    lambda m:m.__setitem__("signed_receipt_schema_version","changed"),
    lambda m:m["projection_fields"].__setitem__(-1,"changed"),
    lambda m:m["projection_field_metadata"][0].__setitem__("type","changed"),
    lambda m:m["projection_field_metadata"][1].__setitem__("required",False),
    lambda m:m["projection_field_metadata"][2].__setitem__("default","changed"),
    lambda m:m["invariants"].__setitem__("phase1_blocked","changed"),
])
def test_manifest_mutations_change_hash_and_fail_reviewed_pin(mutator):
    from projects.alpha_lab.factory.canonical import canonical_hash
    altered=json.loads(cjson(dabl.SCHEMA_MANIFEST_V2))
    mutator(altered)
    assert canonical_hash(altered)!=dabl.REVIEWED_SCHEMA_MANIFEST_SHA256

def test_bad_identity_unverified_binding_and_route_authority_fail_closed():
    # exact syntactic binding stays unauthenticated: there is no local true state.
    assert receipt().externally_authenticated is False
    with pytest.raises(ContractValidationError):dabl.SourceRoute("SRT-2026-001","SRC-2026-001",dabl.SourceRouteKind.PAID,"p","d","o","s",False,False,False,False,False,False,0,0,"e","cannot claim")
    with pytest.raises(ContractValidationError):dabl.SourceRoute("SRT-2026-001","SRC-2026-001",dabl.SourceRouteKind.SELF_COLLECTED,"p","d","o","s",False,False,False,False,False,False,0,0,"e","cannot claim")

def test_unknown_mutable_nonutc_and_ambiguous_na_fail_closed():
    raw=version().to_dict();raw["checkpoint"]="latest"
    with pytest.raises(ContractValidationError):dabl.DataAssetVersion.from_dict(raw)
    with pytest.raises(ContractValidationError):dabl.DataAssetVersion("DAV-2026-001-abcdef123456","DAD-2026-001","SRC-2026-001","SRT-2026-001",H,H,H,{"u":H},dabl.InputImmutability.CREATE_ONLY_IMMUTABLE,license(),H,H,H,NOW.astimezone(dt.timezone(dt.timedelta(hours=-4))),NOW,NOW,NOW,NOW,facts(),("lane-1",))
    bad=facts("NOT_APPLICABLE");bad["pit"]["reason"]="n/a"
    with pytest.raises(ContractValidationError):dabl.DataAssetVersion("DAV-2026-001-abcdef123456","DAD-2026-001","SRC-2026-001","SRT-2026-001",H,H,H,{"u":H},dabl.InputImmutability.CREATE_ONLY_IMMUTABLE,license(),H,H,H,NOW,NOW,NOW,NOW,NOW,bad,("lane-1",))

def test_plan_requires_cas_monotonic_ids_and_preflight_full_projection():
    xs=valid_records()
    with pytest.raises(EventStoreIntegrityError,match="CAS"):dabl.plan_append(xs,expected_previous_head=H,event_id="DABL-EVT-2026-000006",event_type="license_terms",occurred_at=NOW,payload=license().to_dict())
    with pytest.raises(ContractValidationError,match="monotonic"):dabl.plan_append(xs,expected_previous_head=xs[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="license_terms",occurred_at=NOW-dt.timedelta(seconds=1),payload=license().to_dict())
    assert dabl.plan_append(xs,expected_previous_head=xs[-2].event_hash,event_id="DABL-EVT-2026-000005",event_type="asset_version",occurred_at=NOW,payload=version().to_dict())==xs
    with pytest.raises(EventStoreIntegrityError):dabl.plan_append(xs,expected_previous_head=xs[-1].event_hash,event_id="DABL-EVT-2026-000005",event_type="license_terms",occurred_at=NOW,payload=license().to_dict())
    bad=version().to_dict();bad["dependent_lane_ids"]=["different"]
    with pytest.raises(EventStoreIntegrityError):dabl.plan_append(xs[:4],expected_previous_head=xs[3].event_hash,event_id="DABL-EVT-2026-000005",event_type="asset_version",occurred_at=NOW,payload=bad)

def test_projection_revalidates_replay_and_rejects_tampered_stale_objects():
    xs=valid_records();raw=xs[-1].to_dict();raw["payload"]["immutable_bundle_sha256"]="b"*64
    tampered=dabl.DABLEvent.from_dict(raw) if False else raw
    with pytest.raises(EventStoreIntegrityError):dabl.DABLEvent.from_dict(tampered)
    assert dabl.project_event_plan(xs).asset_versions["DAV-2026-001-abcdef123456"].facts["pit"]["status"]=="EVIDENCED"

def test_entry_census_binds_exact_counts_zero_defaults_and_unsigned_qs004_only():
    lanes={"LANE-HYP-2026-{:03d}".format(i):("DAD-2026-{:03d}".format(i),) for i in range(1,14)}
    lanes["LANE-HYP-2026-001"]=("DAD-2026-001",)+tuple("DAD-2026-{:03d}".format(i) for i in range(14,22))
    bound=receipt(); census=dabl.EntryCensus("CENSUS-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,"phase1",H,H,dabl.SCHEMA_MANIFEST_SHA256,H,bound,dabl.canonical_hash(bound.to_dict()) if hasattr(dabl,"canonical_hash") else __import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_hash"]).canonical_hash(bound.to_dict()),("outputs/research/alpha_lab/data_foundry/ledger/"+H,),"rollback",lanes,{"CAND-2026-001":dabl.CandidateDisposition.NO_DATA_ACQUISITION_JUSTIFIED},{"CAND-2026-001":()},{"CAND-2026-001":"owner non-data disposition"},H,True,True,True,True,True,True,True,True,True,True,True,True,True,True)
    assert sum(len(v) for v in census.lane_to_asset_ids.values())==21
    ch=__import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_hash"]).canonical_hash(census.to_dict())
    exact=cjson(census.to_dict())
    assert dabl.QS004DecisionContract("QS004-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,census,exact,ch,bsha(exact),False,None).owner_authorization_present is False
    with pytest.raises(ContractValidationError):dabl.QS004DecisionContract("QS004-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,census,exact,ch,bsha(exact),True,H)

def test_test_only_store_is_temp_confined_cas_safe_and_partial_failure_recovers(tmp_path):
    outside=Path("/private") if Path("/private").exists() else Path("/")
    with pytest.raises(EventStoreIntegrityError):SyntheticPreparationStore(outside/"never-dabl").initialize()
    root=tmp_path/"dabl-test-store";store=SyntheticPreparationStore(root);store.initialize()
    e1=store.append_for_test(expected_previous_head=None,event_id="DABL-EVT-2026-000001",event_type="source_registration",occurred_at=NOW,payload=dabl.SourceRegistration("SRC-2026-001","synthetic","synthetic","owner",H,NOW).to_dict())
    errors=[]
    def add():
        try:store.append_for_test(expected_previous_head=e1.event_hash,event_id="DABL-EVT-2026-000002",event_type="license_terms",occurred_at=NOW,payload=license().to_dict())
        except Exception as e:errors.append(e)
    ts=[threading.Thread(target=add) for _ in range(4)]
    [t.start() for t in ts];[t.join() for t in ts]
    assert len(store.read_all())==2 and not errors
    e2=store.read_all()[-1]
    e3=store.append_for_test(expected_previous_head=e2.event_hash,event_id="DABL-EVT-2026-000003",event_type="asset_definition",occurred_at=NOW,payload=definition().to_dict())
    e4=store.append_for_test(expected_previous_head=e3.event_hash,event_id="DABL-EVT-2026-000004",event_type="route_license_review",occurred_at=NOW,payload=review().to_dict())
    old_retry=store.append_for_test(expected_previous_head=None,event_id="DABL-EVT-2026-000001",event_type="source_registration",occurred_at=NOW,payload=dabl.SourceRegistration("SRC-2026-001","synthetic","synthetic","owner",H,NOW).to_dict())
    assert old_retry.to_dict()==e1.to_dict() and len(store.read_all())==4
    def fail(stage):
        if stage=="after_write_before_publish":raise RuntimeError("injected")
    broken=SyntheticPreparationStore(root,failure_injector=fail)
    with pytest.raises(RuntimeError):broken.append_for_test(expected_previous_head=e4.event_hash,event_id="DABL-EVT-2026-000005",event_type="asset_version",occurred_at=NOW,payload=version().to_dict())
    assert len(store.read_all())==4 and not list(root.glob(".pending-*"))
    failed_root=tmp_path/"failed-init"
    with pytest.raises(RuntimeError):SyntheticPreparationStore(failed_root,failure_injector=lambda stage: (_ for _ in ()).throw(RuntimeError("sentinel")) if stage=="sentinel_write" else None).initialize()
    assert not failed_root.exists()
    SyntheticPreparationStore(failed_root).initialize()
    assert SyntheticPreparationStore(failed_root).read_all()==()

def test_all_typed_events_roundtrip_project_and_critical_links_fail_closed():
    """One deterministic plan exercises every immutable event-map entry."""
    records=()
    def add(event_id,event_type,payload):
        nonlocal records
        records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash if records else None,event_id=event_id,event_type=event_type,occurred_at=NOW,payload=payload.to_dict())
    registration=dabl.SourceRegistration("SRC-2026-001","synthetic","synthetic","owner",H,NOW);add("DABL-EVT-2026-000001","source_registration",registration)
    lic=license();add("DABL-EVT-2026-000002","license_terms",lic)
    definition_value=definition();add("DABL-EVT-2026-000003","asset_definition",definition_value)
    review_value=review();add("DABL-EVT-2026-000004","route_license_review",review_value)
    first=version();add("DABL-EVT-2026-000005","asset_version",first)
    requirement=dabl.IndependentReplayRequirement("RPL-2026-001",first.asset_version_id,H,"independent-reviewer",NOW,None,None);add("DABL-EVT-2026-000006","replay_requirement",requirement)
    replay=dabl.IndependentReplayReceipt("RRC-2026-001","RPL-2026-001",first.asset_version_id,H,H,H,H,H,H,records[-1].event_hash,"producer","independent-reviewer",NOW);add("DABL-EVT-2026-000007","independent_replay_receipt",replay)
    cert=dabl.DataCertification("DCE-2026-001",first.asset_version_id,dabl.DataTier.A,dabl.CertificationStatus.DRAFT_UNVERIFIED,"certifier",None,NOW,ref(),H,requirement,__import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_hash"]).canonical_hash(replay.to_dict()),NOW+dt.timedelta(days=1),"draft");add("DABL-EVT-2026-000008","certification",cert)
    blocker=dabl.DataBlocker("DBL-2026-001",definition_value.asset_definition_id,ref(),"claim",dabl.BlockerCategory.PIT,"owner",dabl.BlockerSeverity.HIGH,dabl.BlockerStatus.OPEN,("pit",),"reason","action",("SRT-2026-001",),0.0,1.0,"accept",NOW,None,NOW);add("DABL-EVT-2026-000009","blocker",blocker)
    second=__import__("dataclasses").replace(first,asset_version_id="DAV-2026-001-bcdefa123456")
    add("DABL-EVT-2026-000011","asset_version",second)
    readiness=dabl.DataReadinessPacket("DRP-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,ref(),("DAD-2026-001",),("DCE-2026-001",),dabl.DataTier.A,dabl.NextAction.PREPARE_OWNER_REVIEW,NOW);add("DABL-EVT-2026-000015","readiness_packet",readiness)
    gap=dabl.EvidenceGapPacket("EGP-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,ref(),("DBL-2026-001",),(route(),),dabl.DataTier.C,dabl.CandidateDisposition.EVIDENCE_GAP,dabl.NextAction.REMEDIATE_FACTS,NOW);add("DABL-EVT-2026-000016","evidence_gap_packet",gap)
    transition=dabl.BlockerTransition("DBT-2026-001","DBL-2026-001",dabl.BlockerStatus.OPEN,dabl.BlockerStatus.IN_REVIEW,"review",NOW);add("DABL-EVT-2026-000010","blocker_transition",transition)
    supersession=dabl.SupersessionRecord("SUP-2026-001",first.asset_version_id,second.asset_version_id,"replacement",NOW);add("DABL-EVT-2026-000012","supersession",supersession)
    signed=dabl.SignedProjectionEvent("PRJ-2026-001",receipt(),H,NOW);add("DABL-EVT-2026-000017","signed_projection",signed)
    packet=dabl.SignedPacketEvent("PSG-2026-001","DRP-2026-001",__import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_hash"]).canonical_hash(readiness.to_dict()),"signer",H,receipt(),dabl.ExternalVerificationState.EXTERNAL_VERIFIER_REQUIRED,NOW);add("DABL-EVT-2026-000018","signed_packet",packet)
    revocation=dabl.RevocationRecord("REV-2026-001",first.asset_version_id,"retracted",NOW,H);add("DABL-EVT-2026-000019","revocation",revocation)
    status=dabl.DataStatusRecord("DST-2026-001",first.asset_version_id,dabl.CertificationStatus.SUPERSEDED,NOW,"replaced",None,"SUP-2026-001");add("DABL-EVT-2026-000020","status",status)
    lanes={"LANE-HYP-2026-{:03d}".format(i):("DAD-2026-{:03d}".format(i),) for i in range(1,14)}
    lanes["LANE-HYP-2026-001"]=("DAD-2026-001",)+tuple("DAD-2026-{:03d}".format(i) for i in range(14,22))
    # Synthetic, non-authoritative materialized census: 21 exact definitions across 13 lanes.
    for number in range(2,22):
        asset_id="DAD-2026-{:03d}".format(number)
        lane_id="LANE-HYP-2026-001" if number>=14 else "LANE-HYP-2026-{:03d}".format(number)
        unique_route=__import__("dataclasses").replace(route(),route_id="SRT-2026-{:03d}".format(number))
        synthetic_definition=__import__("dataclasses").replace(definition_value,asset_definition_id=asset_id,name="synthetic-{}".format(number),source_routes=(unique_route,),dependent_lane_ids=(lane_id,))
        add("DABL-EVT-2026-{:06d}".format(100+number),"asset_definition",synthetic_definition)
    census=dabl.EntryCensus("CENSUS-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,"phase1",H,H,dabl.SCHEMA_MANIFEST_SHA256,H,receipt(),__import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_hash"]).canonical_hash(receipt().to_dict()),("outputs/research/alpha_lab/data_foundry/ledger/"+H,),"rollback",lanes,{"CAND-2026-001":dabl.CandidateDisposition.NO_DATA_ACQUISITION_JUSTIFIED},{"CAND-2026-001":()},{"CAND-2026-001":"owner non-data disposition"},H,True,True,True,True,True,True,True,True,True,True,True,True,True,True)
    add("DABL-EVT-2026-000021","entry_census",census)
    exact=cjson(census.to_dict());qs=dabl.QS004DecisionContract("QS004-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,census,exact,__import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_hash"]).canonical_hash(census.to_dict()),bsha(exact),False,None)
    add("DABL-EVT-2026-000022","qs004_decision",qs)
    projection=dabl.project_event_plan(records)
    assert projection.source_registrations and projection.route_license_reviews and projection.replay_receipts and projection.signed_projections and projection.signed_packets and projection.blocker_transitions and projection.entry_censuses and projection.qs004_decisions
    assert dabl.SCHEMA_MANIFEST_V2["event_mapping"] and dabl.verify_schema_manifest()
    with pytest.raises(ContractValidationError):dabl.SignedExportBindingReceipt("wrong","SIGNED_RESEARCH_ONLY_NONEXECUTIONAL",NOW,"/absolute","wrong",H,1,H,{"0":None,"1":H},NOW,True,True,H,True,H,H,H,H,H,H,H)
    mutated=first.to_dict();mutated["source_route_id"]="SRT-2026-999"
    with pytest.raises(EventStoreIntegrityError):dabl.plan_append(records[:4],expected_previous_head=records[3].event_hash,event_id="DABL-EVT-2026-000005",event_type="asset_version",occurred_at=NOW,payload=mutated)

def test_third_audit_regressions_fail_closed_for_source_time_receipt_qs_and_retry():
    """Direct reproductions for the third-audit source, time, receipt, and retry findings."""
    # A definition cannot materialize without its exact source registration.
    with pytest.raises(EventStoreIntegrityError):
        dabl.plan_append((),expected_previous_head=None,event_id="DABL-EVT-2026-000001",event_type="asset_definition",occurred_at=NOW,payload=definition().to_dict())
    records=()
    def add(eid,kind,value):
        nonlocal records
        records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash if records else None,event_id=eid,event_type=kind,occurred_at=NOW,payload=value.to_dict())
    add("DABL-EVT-2026-000001","source_registration",dabl.SourceRegistration("SRC-2026-001","synthetic","synthetic","owner",H,NOW))
    add("DABL-EVT-2026-000002","license_terms",license())
    add("DABL-EVT-2026-000003","asset_definition",definition())
    # A registered provider alone does not substitute for route-specific license review.
    with pytest.raises(EventStoreIntegrityError):
        dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000004",event_type="asset_version",occurred_at=NOW,payload=version().to_dict())
    late=__import__("dataclasses").replace(dabl.DataBlocker("DBL-2026-001","DAD-2026-001",ref(),"claim",dabl.BlockerCategory.PIT,"owner",dabl.BlockerSeverity.HIGH,dabl.BlockerStatus.OPEN,("pit",),"reason","action",("SRT-2026-001",),0.0,1.0,"accept",NOW+dt.timedelta(days=2),None,NOW+dt.timedelta(days=1)))
    with pytest.raises(EventStoreIntegrityError,match="recorded_at"):
        dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000004",event_type="blocker",occurred_at=NOW,payload=late.to_dict())
    # An idempotent retry must use the event's original predecessor; a later head fails.
    full=valid_records()
    with pytest.raises(EventStoreIntegrityError):
        dabl.plan_append(full,expected_previous_head=full[-1].event_hash,event_id="DABL-EVT-2026-000005",event_type="asset_version",occurred_at=NOW,payload=version().to_dict())
    bad_empty={"0":H}
    with pytest.raises(ContractValidationError):
        dabl.SignedExportBindingReceipt("caerus_alpha_lab_signed_projection_export_v1","SIGNED_RESEARCH_ONLY_NON_EXECUTIONAL",NOW,"projects/alpha_lab/ledger/events.jsonl","caerus_alpha_lab_source_ledger_receipt_v1",H,0,None,bad_empty,None,True,True,H,False,H,H,H,H,H,H,H)
    lanes={"LANE-HYP-2026-{:03d}".format(i):("DAD-2026-{:03d}".format(i),) for i in range(1,14)};lanes["LANE-HYP-2026-001"]=("DAD-2026-001",)+tuple("DAD-2026-{:03d}".format(i) for i in range(14,22))
    census=dabl.EntryCensus("CENSUS-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,"phase1",H,H,dabl.SCHEMA_MANIFEST_SHA256,H,receipt(),__import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_hash"]).canonical_hash(receipt().to_dict()),("outputs/research/alpha_lab/data_foundry/ledger/"+H,),"rollback",lanes,{"CAND-2026-001":dabl.CandidateDisposition.NO_DATA_ACQUISITION_JUSTIFIED},{"CAND-2026-001":()},{"CAND-2026-001":"owner non-data disposition"},H,True,True,True,True,True,True,True,True,True,True,True,True,True,True)
    exact=cjson(census.to_dict())
    with pytest.raises(ContractValidationError):
        dabl.QS004DecisionContract("QS004-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,census,exact+" ",__import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_hash"]).canonical_hash(census.to_dict()),bsha(exact),False,None)
    assert dabl.SCHEMA_MANIFEST_SHA256==dabl.REVIEWED_SCHEMA_MANIFEST_SHA256 and dabl.verify_schema_manifest()
    assert "DABLProjection" not in dabl.__all__

def test_tier_a_receipt_current_state_and_open_blocker_regressions_fail_closed():
    """Tier A can only be a blocked draft after exact replay and current-state checks."""
    records=()
    def add(eid,kind,value):
        nonlocal records
        records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash if records else None,event_id=eid,event_type=kind,occurred_at=NOW,payload=value.to_dict())
    add("DABL-EVT-2026-000001","source_registration",dabl.SourceRegistration("SRC-2026-001","synthetic","synthetic","owner",H,NOW))
    add("DABL-EVT-2026-000002","license_terms",license())
    add("DABL-EVT-2026-000003","asset_definition",definition())
    add("DABL-EVT-2026-000004","route_license_review",review());asset=version();add("DABL-EVT-2026-000005","asset_version",asset)
    requirement=dabl.IndependentReplayRequirement("RPL-2026-001",asset.asset_version_id,H,"independent-reviewer",NOW,None,None);add("DABL-EVT-2026-000006","replay_requirement",requirement)
    replay=dabl.IndependentReplayReceipt("RRC-2026-001","RPL-2026-001",asset.asset_version_id,H,H,H,H,H,H,records[-1].event_hash,"producer","independent-reviewer",NOW);add("DABL-EVT-2026-000007","independent_replay_receipt",replay)
    good_hash=__import__("projects.alpha_lab.factory.canonical",fromlist=["canonical_hash"]).canonical_hash(replay.to_dict())
    bad_cert=dabl.DataCertification("DCE-2026-001",asset.asset_version_id,dabl.DataTier.A,dabl.CertificationStatus.DRAFT_UNVERIFIED,"certifier",None,NOW,ref(),H,requirement,H,NOW+dt.timedelta(days=1),"draft")
    with pytest.raises(EventStoreIntegrityError,match="receipt"):
        dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000008",event_type="certification",occurred_at=NOW,payload=bad_cert.to_dict())
    cert=__import__("dataclasses").replace(bad_cert,independent_replay_receipt_sha256=good_hash);add("DABL-EVT-2026-000008","certification",cert)
    ready=dabl.DataReadinessPacket("DRP-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,ref(),("DAD-2026-001",),("DCE-2026-001",),dabl.DataTier.A,dabl.NextAction.PREPARE_OWNER_REVIEW,NOW);add("DABL-EVT-2026-000009","readiness_packet",ready)
    add("DABL-EVT-2026-000010","revocation",dabl.RevocationRecord("REV-2026-001",asset.asset_version_id,"retracted",NOW,H))
    stale=__import__("dataclasses").replace(ready,packet_id="DRP-2026-002")
    with pytest.raises(EventStoreIntegrityError,match="revoked"):
        dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000011",event_type="readiness_packet",occurred_at=NOW,payload=stale.to_dict())
    blocker=dabl.DataBlocker("DBL-2026-001","DAD-2026-001",ref(),"claim",dabl.BlockerCategory.PIT,"owner",dabl.BlockerSeverity.HIGH,dabl.BlockerStatus.OPEN,("pit",),"reason","action",("SRT-2026-001",),0.0,1.0,"accept",NOW,None,NOW)
    # A separate valid plan proves an EGP cannot cite a transitioned blocker.
    base=records[:4];base=dabl.plan_append(base,expected_previous_head=base[-1].event_hash,event_id="DABL-EVT-2026-000012",event_type="blocker",occurred_at=NOW,payload=blocker.to_dict())
    transition=dabl.BlockerTransition("DBT-2026-001","DBL-2026-001",dabl.BlockerStatus.OPEN,dabl.BlockerStatus.IN_REVIEW,"review",NOW)
    base=dabl.plan_append(base,expected_previous_head=base[-1].event_hash,event_id="DABL-EVT-2026-000013",event_type="blocker_transition",occurred_at=NOW,payload=transition.to_dict())
    gap=dabl.EvidenceGapPacket("EGP-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,ref(),("DBL-2026-001",),(route(),),dabl.DataTier.C,dabl.CandidateDisposition.EVIDENCE_GAP,dabl.NextAction.REMEDIATE_FACTS,NOW)
    with pytest.raises(EventStoreIntegrityError,match="gap"):
        dabl.plan_append(base,expected_previous_head=base[-1].event_hash,event_id="DABL-EVT-2026-000014",event_type="evidence_gap_packet",occurred_at=NOW,payload=gap.to_dict())


@pytest.mark.parametrize("path",(
    "./projects/alpha_lab/ledger/events.jsonl",
    "projects/./alpha_lab/ledger/events.jsonl",
    "projects/alpha_lab/../ledger/events.jsonl",
    "projects//alpha_lab/ledger/events.jsonl",
    "projects/alpha_lab/ledger/.",
))
def test_signed_export_binding_rejects_dot_and_separator_path_aliases(path):
    with pytest.raises(ContractValidationError,match="normalized repo-relative"):
        dabl.SignedExportBindingReceipt("caerus_alpha_lab_signed_projection_export_v1","SIGNED_RESEARCH_ONLY_NON_EXECUTIONAL",NOW,path,"caerus_alpha_lab_source_ledger_receipt_v1",H,1,H,{"0":None,"1":H},NOW,True,True,H,True,H,H,H,H,H,H,H)


def test_replay_receipt_cannot_predate_asset_model_availability_or_request():
    records=valid_records()
    asset=version()
    requirement=dabl.IndependentReplayRequirement("RPL-2026-001",asset.asset_version_id,H,"independent-reviewer",NOW,None,None)
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="replay_requirement",occurred_at=NOW,payload=requirement.to_dict())
    before_model=dabl.IndependentReplayReceipt("RRC-2026-001",requirement.replay_id,asset.asset_version_id,H,H,H,H,H,H,records[-1].event_hash,"producer","independent-reviewer",NOW-dt.timedelta(seconds=1))
    with pytest.raises(EventStoreIntegrityError,match="replay receipt binding"):
        dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="independent_replay_receipt",occurred_at=NOW,payload=before_model.to_dict())

    later_request=replace(requirement,requested_at=NOW+dt.timedelta(hours=2))
    requested=dabl.plan_append(valid_records(),expected_previous_head=valid_records()[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="replay_requirement",occurred_at=later_request.requested_at,payload=later_request.to_dict())
    before_request=replace(before_model,completed_at=NOW+dt.timedelta(hours=1),dabl_head_hash=requested[-1].event_hash)
    with pytest.raises(EventStoreIntegrityError,match="replay receipt binding"):
        dabl.plan_append(requested,expected_previous_head=requested[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="independent_replay_receipt",occurred_at=later_request.requested_at,payload=before_request.to_dict())


def test_certification_causal_bounds_cover_asset_license_replay_and_readiness():
    records=valid_records();asset=version()
    requirement=dabl.IndependentReplayRequirement("RPL-2026-001",asset.asset_version_id,H,"independent-reviewer",NOW,None,None)
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="replay_requirement",occurred_at=NOW,payload=requirement.to_dict())
    before_asset=dabl.DataCertification("DCE-2026-001",asset.asset_version_id,dabl.DataTier.B,dabl.CertificationStatus.DRAFT_UNVERIFIED,"certifier",None,NOW-dt.timedelta(seconds=1),ref(),H,requirement,None,NOW+dt.timedelta(days=1),"draft")
    with pytest.raises(EventStoreIntegrityError,match="causal lower bound"):
        dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="certification",occurred_at=NOW,payload=before_asset.to_dict())

    early=NOW-dt.timedelta(days=2);accepted=NOW-dt.timedelta(days=1);certified=early+dt.timedelta(hours=12)
    accepted_license=replace(license(),accepted_at=accepted)
    accepted_version=replace(asset,license_terms=accepted_license,availability_at=early,effective_at=early,retrieved_at=early,ingested_at=early,model_available_at=early)
    licensed=()
    def add_licensed(eid,kind,value):
        nonlocal licensed
        licensed=dabl.plan_append(licensed,expected_previous_head=licensed[-1].event_hash if licensed else None,event_id=eid,event_type=kind,occurred_at=NOW,payload=value.to_dict())
    add_licensed("DABL-EVT-2026-000001","source_registration",dabl.SourceRegistration("SRC-2026-001","synthetic","synthetic","owner",H,NOW))
    add_licensed("DABL-EVT-2026-000002","license_terms",accepted_license)
    add_licensed("DABL-EVT-2026-000003","asset_definition",definition())
    add_licensed("DABL-EVT-2026-000004","route_license_review",review())
    add_licensed("DABL-EVT-2026-000005","asset_version",accepted_version)
    early_requirement=replace(requirement,requested_at=early)
    add_licensed("DABL-EVT-2026-000006","replay_requirement",early_requirement)
    before_license=replace(before_asset,certified_at=certified,independent_replay=early_requirement,freshness_deadline=NOW+dt.timedelta(days=1))
    with pytest.raises(EventStoreIntegrityError,match="causal lower bound"):
        dabl.plan_append(licensed,expected_previous_head=licensed[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="certification",occurred_at=NOW,payload=before_license.to_dict())

    completed=NOW+dt.timedelta(hours=2)
    replay=dabl.IndependentReplayReceipt("RRC-2026-001",requirement.replay_id,asset.asset_version_id,H,H,H,H,H,H,records[-1].event_hash,"producer","independent-reviewer",completed)
    replayed=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="independent_replay_receipt",occurred_at=completed,payload=replay.to_dict())
    before_replay=dabl.DataCertification("DCE-2026-001",asset.asset_version_id,dabl.DataTier.A,dabl.CertificationStatus.DRAFT_UNVERIFIED,"certifier",None,NOW+dt.timedelta(hours=1),ref(),H,requirement,canonical_hash(replay.to_dict()),NOW+dt.timedelta(days=1),"draft")
    with pytest.raises(EventStoreIntegrityError,match="causal lower bound"):
        dabl.plan_append(replayed,expected_previous_head=replayed[-1].event_hash,event_id="DABL-EVT-2026-000008",event_type="certification",occurred_at=completed,payload=before_replay.to_dict())

    certified_at=NOW+dt.timedelta(hours=1)
    certification=replace(before_asset,certified_at=certified_at,freshness_deadline=NOW+dt.timedelta(days=1))
    certified_records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="certification",occurred_at=certified_at,payload=certification.to_dict())
    premature=dabl.DataReadinessPacket("DRP-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,ref(),("DAD-2026-001",),("DCE-2026-001",),dabl.DataTier.B,dabl.NextAction.PREPARE_OWNER_REVIEW,NOW)
    with pytest.raises(EventStoreIntegrityError,match="readiness causal lower bound"):
        dabl.plan_append(certified_records,expected_previous_head=certified_records[-1].event_hash,event_id="DABL-EVT-2026-000008",event_type="readiness_packet",occurred_at=certified_at,payload=premature.to_dict())


def test_tier_b_certification_cannot_predate_model_request_fact_or_cited_receipt():
    asset=version();records=valid_records()
    request_at=NOW+dt.timedelta(hours=2)
    requirement=dabl.IndependentReplayRequirement("RPL-2026-001",asset.asset_version_id,H,"independent-reviewer",request_at,None,None)
    requested=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="replay_requirement",occurred_at=request_at,payload=requirement.to_dict())
    before_request=dabl.DataCertification("DCE-2026-001",asset.asset_version_id,dabl.DataTier.B,dabl.CertificationStatus.DRAFT_UNVERIFIED,"certifier",None,NOW+dt.timedelta(hours=1),ref(),H,requirement,None,NOW+dt.timedelta(days=1),"draft")
    with pytest.raises(EventStoreIntegrityError,match="causal lower bound"):
        dabl.plan_append(requested,expected_previous_head=requested[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="certification",occurred_at=request_at,payload=before_request.to_dict())

    fact_at=NOW+dt.timedelta(hours=3)
    late_facts=facts()
    for fact in late_facts.values():fact["observed_at"]="2026-08-23T15:00:00Z"
    fact_asset=replace(asset,facts=late_facts)
    fact_records=valid_records()[:4]
    fact_records=dabl.plan_append(fact_records,expected_previous_head=fact_records[-1].event_hash,event_id="DABL-EVT-2026-000005",event_type="asset_version",occurred_at=fact_at,payload=fact_asset.to_dict())
    fact_requirement=replace(requirement,requested_at=NOW)
    fact_records=dabl.plan_append(fact_records,expected_previous_head=fact_records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="replay_requirement",occurred_at=fact_at,payload=fact_requirement.to_dict())
    before_fact=replace(before_request,independent_replay=fact_requirement,certified_at=NOW+dt.timedelta(hours=2),freshness_deadline=NOW+dt.timedelta(days=1))
    with pytest.raises(EventStoreIntegrityError,match="causal lower bound"):
        dabl.plan_append(fact_records,expected_previous_head=fact_records[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="certification",occurred_at=fact_at,payload=before_fact.to_dict())

    base=valid_records();base_requirement=replace(requirement,requested_at=NOW)
    base=dabl.plan_append(base,expected_previous_head=base[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="replay_requirement",occurred_at=NOW,payload=base_requirement.to_dict())
    completed=NOW+dt.timedelta(hours=2)
    replay=dabl.IndependentReplayReceipt("RRC-2026-001",base_requirement.replay_id,asset.asset_version_id,H,H,H,H,H,H,base[-1].event_hash,"producer","independent-reviewer",completed)
    replayed=dabl.plan_append(base,expected_previous_head=base[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="independent_replay_receipt",occurred_at=completed,payload=replay.to_dict())
    before_receipt=dabl.DataCertification("DCE-2026-001",asset.asset_version_id,dabl.DataTier.B,dabl.CertificationStatus.DRAFT_UNVERIFIED,"certifier",None,NOW+dt.timedelta(hours=1),ref(),H,base_requirement,canonical_hash(replay.to_dict()),NOW+dt.timedelta(days=1),"draft")
    with pytest.raises(EventStoreIntegrityError,match="causal lower bound"):
        dabl.plan_append(replayed,expected_previous_head=replayed[-1].event_hash,event_id="DABL-EVT-2026-000008",event_type="certification",occurred_at=completed,payload=before_receipt.to_dict())

    model_at=NOW+dt.timedelta(hours=4)
    model_asset=replace(asset,availability_at=NOW,retrieved_at=NOW+dt.timedelta(hours=1),ingested_at=NOW+dt.timedelta(hours=2),model_available_at=model_at)
    model_records=valid_records()[:4]
    model_records=dabl.plan_append(model_records,expected_previous_head=model_records[-1].event_hash,event_id="DABL-EVT-2026-000005",event_type="asset_version",occurred_at=model_at,payload=model_asset.to_dict())
    model_requirement=replace(requirement,requested_at=model_at)
    model_records=dabl.plan_append(model_records,expected_previous_head=model_records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="replay_requirement",occurred_at=model_at,payload=model_requirement.to_dict())
    before_model=replace(before_request,independent_replay=model_requirement,certified_at=NOW+dt.timedelta(hours=3),freshness_deadline=NOW+dt.timedelta(days=1))
    with pytest.raises(EventStoreIntegrityError,match="causal lower bound"):
        dabl.plan_append(model_records,expected_previous_head=model_records[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="certification",occurred_at=model_at,payload=before_model.to_dict())


def test_blocked_certification_serializes_blocked_before_asset_overrides():
    records=valid_records();asset=version()
    requirement=dabl.IndependentReplayRequirement("RPL-2026-001",asset.asset_version_id,H,"independent-reviewer",NOW,None,None)
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="replay_requirement",occurred_at=NOW,payload=requirement.to_dict())
    certification=dabl.DataCertification("DCE-2026-001",asset.asset_version_id,dabl.DataTier.B,dabl.CertificationStatus.BLOCKED,"certifier",None,NOW,ref(),H,requirement,None,NOW+dt.timedelta(days=1),"blocked draft")
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="certification",occurred_at=NOW,payload=certification.to_dict())
    projection=dabl.project_event_plan(records).to_dict()
    assert projection["asset_current_statuses"]=={asset.asset_version_id:"DRAFT_UNVERIFIED"}
    assert projection["certification_current_statuses"]=={"DCE-2026-001":"BLOCKED"}


def test_transition_and_status_semantic_times_cannot_backdate_predecessors():
    base=valid_records()[:4]
    created_at=NOW+dt.timedelta(hours=2)
    blocker=dabl.DataBlocker("DBL-2026-001","DAD-2026-001",ref(),"claim",dabl.BlockerCategory.PIT,"owner",dabl.BlockerSeverity.HIGH,dabl.BlockerStatus.OPEN,("pit",),"reason","action",("SRT-2026-001",),0.0,1.0,"accept",created_at,None,created_at)
    blocked=dabl.plan_append(base,expected_previous_head=base[-1].event_hash,event_id="DABL-EVT-2026-000005",event_type="blocker",occurred_at=created_at,payload=blocker.to_dict())
    before_creation=dabl.BlockerTransition("DBT-2026-001",blocker.blocker_id,dabl.BlockerStatus.OPEN,dabl.BlockerStatus.IN_REVIEW,"backdated",NOW+dt.timedelta(hours=1))
    with pytest.raises(EventStoreIntegrityError,match="blocker transition"):
        dabl.plan_append(blocked,expected_previous_head=blocked[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="blocker_transition",occurred_at=created_at,payload=before_creation.to_dict())

    first=dabl.BlockerTransition("DBT-2026-001",blocker.blocker_id,dabl.BlockerStatus.OPEN,dabl.BlockerStatus.IN_REVIEW,"review",created_at)
    transitioned=dabl.plan_append(blocked,expected_previous_head=blocked[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="blocker_transition",occurred_at=created_at,payload=first.to_dict())
    regressed=dabl.BlockerTransition("DBT-2026-002",blocker.blocker_id,dabl.BlockerStatus.IN_REVIEW,dabl.BlockerStatus.REOPENED,"regressed",NOW+dt.timedelta(hours=1))
    with pytest.raises(EventStoreIntegrityError,match="blocker transition"):
        dabl.plan_append(transitioned,expected_previous_head=transitioned[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="blocker_transition",occurred_at=created_at,payload=regressed.to_dict())

    records=valid_records();asset_id=version().asset_version_id
    first_status=dabl.DataStatusRecord("DST-2026-001",asset_id,dabl.CertificationStatus.BLOCKED,created_at,"blocked",None,None)
    statuses=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="status",occurred_at=created_at,payload=first_status.to_dict())
    regressed_status=dabl.DataStatusRecord("DST-2026-002",asset_id,dabl.CertificationStatus.DRAFT_UNVERIFIED,NOW+dt.timedelta(hours=1),"backdated",None,None)
    with pytest.raises(EventStoreIntegrityError,match="status binding"):
        dabl.plan_append(statuses,expected_previous_head=statuses[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="status",occurred_at=created_at,payload=regressed_status.to_dict())

    revocation=dabl.RevocationRecord("REV-2026-001",asset_id,"retracted",created_at,H)
    revoked=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="revocation",occurred_at=created_at,payload=revocation.to_dict())
    early_revoked_status=dabl.DataStatusRecord("DST-2026-001",asset_id,dabl.CertificationStatus.REVOKED,NOW+dt.timedelta(hours=1),"backdated","REV-2026-001",None)
    with pytest.raises(EventStoreIntegrityError,match="status binding"):
        dabl.plan_append(revoked,expected_previous_head=revoked[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="status",occurred_at=created_at,payload=early_revoked_status.to_dict())

    second=replace(version(),asset_version_id="DAV-2026-001-bcdefa123456")
    paired=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="asset_version",occurred_at=NOW,payload=second.to_dict())
    supersession=dabl.SupersessionRecord("SUP-2026-001",asset_id,second.asset_version_id,"replaced",created_at)
    superseded=dabl.plan_append(paired,expected_previous_head=paired[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="supersession",occurred_at=created_at,payload=supersession.to_dict())
    early_superseded_status=dabl.DataStatusRecord("DST-2026-001",asset_id,dabl.CertificationStatus.SUPERSEDED,NOW+dt.timedelta(hours=1),"backdated",None,"SUP-2026-001")
    with pytest.raises(EventStoreIntegrityError,match="status binding"):
        dabl.plan_append(superseded,expected_previous_head=superseded[-1].event_hash,event_id="DABL-EVT-2026-000008",event_type="status",occurred_at=created_at,payload=early_superseded_status.to_dict())


def test_initial_status_gap_and_packet_signature_cannot_backdate_dependencies():
    model_at=NOW+dt.timedelta(hours=2)
    asset=replace(version(),retrieved_at=NOW,ingested_at=NOW+dt.timedelta(hours=1),model_available_at=model_at)
    records=valid_records()[:4]
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000005",event_type="asset_version",occurred_at=model_at,payload=asset.to_dict())
    early_status=dabl.DataStatusRecord("DST-2026-001",asset.asset_version_id,dabl.CertificationStatus.BLOCKED,NOW+dt.timedelta(hours=1),"backdated",None,None)
    with pytest.raises(EventStoreIntegrityError,match="status binding"):
        dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="status",occurred_at=model_at,payload=early_status.to_dict())

    blocker=dabl.DataBlocker("DBL-2026-001","DAD-2026-001",ref(),"claim",dabl.BlockerCategory.PIT,"owner",dabl.BlockerSeverity.HIGH,dabl.BlockerStatus.OPEN,("pit",),"reason","action",("SRT-2026-001",),0.0,1.0,"accept",model_at,None,model_at)
    blocked=dabl.plan_append(valid_records()[:4],expected_previous_head=valid_records()[3].event_hash,event_id="DABL-EVT-2026-000005",event_type="blocker",occurred_at=model_at,payload=blocker.to_dict())
    early_gap=dabl.EvidenceGapPacket("EGP-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,ref(),(blocker.blocker_id,),(route(),),dabl.DataTier.C,dabl.CandidateDisposition.EVIDENCE_GAP,dabl.NextAction.REMEDIATE_FACTS,NOW+dt.timedelta(hours=1))
    with pytest.raises(EventStoreIntegrityError,match="gap causal lower bound"):
        dabl.plan_append(blocked,expected_previous_head=blocked[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="evidence_gap_packet",occurred_at=model_at,payload=early_gap.to_dict())

    first=dabl.BlockerTransition("DBT-2026-001",blocker.blocker_id,dabl.BlockerStatus.OPEN,dabl.BlockerStatus.IN_REVIEW,"review",model_at+dt.timedelta(hours=1))
    transitioned=dabl.plan_append(blocked,expected_previous_head=blocked[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="blocker_transition",occurred_at=first.transitioned_at,payload=first.to_dict())
    reopened_at=model_at+dt.timedelta(hours=3)
    reopened_transition=dabl.BlockerTransition("DBT-2026-002",blocker.blocker_id,dabl.BlockerStatus.IN_REVIEW,dabl.BlockerStatus.REOPENED,"reopened",reopened_at)
    reopened=dabl.plan_append(transitioned,expected_previous_head=transitioned[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="blocker_transition",occurred_at=reopened_at,payload=reopened_transition.to_dict())
    before_latest=replace(early_gap,created_at=model_at+dt.timedelta(hours=2))
    with pytest.raises(EventStoreIntegrityError,match="gap causal lower bound"):
        dabl.plan_append(reopened,expected_previous_head=reopened[-1].event_hash,event_id="DABL-EVT-2026-000008",event_type="evidence_gap_packet",occurred_at=reopened_at,payload=before_latest.to_dict())

    packet_at=NOW+dt.timedelta(hours=2)
    simple_blocker=replace(blocker,created_at=NOW,review_by=NOW)
    packet_records=dabl.plan_append(valid_records()[:4],expected_previous_head=valid_records()[3].event_hash,event_id="DABL-EVT-2026-000005",event_type="blocker",occurred_at=NOW,payload=simple_blocker.to_dict())
    gap=replace(early_gap,created_at=packet_at)
    packet_records=dabl.plan_append(packet_records,expected_previous_head=packet_records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="evidence_gap_packet",occurred_at=packet_at,payload=gap.to_dict())
    packet_hash=canonical_hash(gap.to_dict())
    before_packet=dabl.SignedPacketEvent("PSG-2026-001",gap.packet_id,packet_hash,"signer",H,receipt(),dabl.ExternalVerificationState.EXTERNAL_VERIFIER_REQUIRED,NOW+dt.timedelta(hours=1))
    with pytest.raises(EventStoreIntegrityError,match="signed packet"):
        dabl.plan_append(packet_records,expected_previous_head=packet_records[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="signed_packet",occurred_at=packet_at,payload=before_packet.to_dict())
    after_recorded=replace(before_packet,signed_at=packet_at+dt.timedelta(hours=1))
    with pytest.raises(EventStoreIntegrityError,match="recorded_at"):
        dabl.plan_append(packet_records,expected_previous_head=packet_records[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="signed_packet",occurred_at=packet_at,payload=after_recorded.to_dict())


@pytest.mark.parametrize("altered_route",(
    replace(route(),exact_scope="altered synthetic scope"),
    replace(route(),estimated_cost_usd=2.0),
    replace(route(),residual_claim="cannot make the originally registered residual claim"),
))
def test_evidence_gap_rejects_same_id_altered_immutable_route(altered_route):
    records=valid_records()[:4]
    blocker=dabl.DataBlocker("DBL-2026-001","DAD-2026-001",ref(),"claim",dabl.BlockerCategory.PIT,"owner",dabl.BlockerSeverity.HIGH,dabl.BlockerStatus.OPEN,("pit",),"reason","action",("SRT-2026-001",),0.0,1.0,"accept",NOW,None,NOW)
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000005",event_type="blocker",occurred_at=NOW,payload=blocker.to_dict())
    gap=dabl.EvidenceGapPacket("EGP-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,ref(),(blocker.blocker_id,),(altered_route,),dabl.DataTier.C,dabl.CandidateDisposition.EVIDENCE_GAP,dabl.NextAction.REMEDIATE_FACTS,NOW)
    with pytest.raises(EventStoreIntegrityError,match="exact blocker/definition route"):
        dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="evidence_gap_packet",occurred_at=NOW,payload=gap.to_dict())


def test_evidence_gap_route_set_equals_cited_blocker_route_union():
    second_route=replace(route(),route_id="SRT-2026-002",exact_scope="second exact scope")
    definition_with_two=replace(definition(),source_routes=(route(),second_route))
    records=()
    records=dabl.plan_append(records,expected_previous_head=None,event_id="DABL-EVT-2026-000001",event_type="source_registration",occurred_at=NOW,payload=dabl.SourceRegistration("SRC-2026-001","synthetic","synthetic","owner",H,NOW).to_dict())
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000002",event_type="asset_definition",occurred_at=NOW,payload=definition_with_two.to_dict())
    blocker=dabl.DataBlocker("DBL-2026-001","DAD-2026-001",ref(),"claim",dabl.BlockerCategory.PIT,"owner",dabl.BlockerSeverity.HIGH,dabl.BlockerStatus.OPEN,("pit",),"reason","action",("SRT-2026-001",),0.0,1.0,"accept",NOW,None,NOW)
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000003",event_type="blocker",occurred_at=NOW,payload=blocker.to_dict())
    extra=dabl.EvidenceGapPacket("EGP-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,ref(),(blocker.blocker_id,),(route(),second_route),dabl.DataTier.C,dabl.CandidateDisposition.EVIDENCE_GAP,dabl.NextAction.REMEDIATE_FACTS,NOW)
    with pytest.raises(EventStoreIntegrityError,match="exact blocker/definition route"):
        dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000004",event_type="evidence_gap_packet",occurred_at=NOW,payload=extra.to_dict())

    blocker_both=replace(blocker,route_ids=("SRT-2026-001","SRT-2026-002"))
    both=records[:2]
    both=dabl.plan_append(both,expected_previous_head=both[-1].event_hash,event_id="DABL-EVT-2026-000003",event_type="blocker",occurred_at=NOW,payload=blocker_both.to_dict())
    missing=replace(extra,source_routes=(route(),))
    with pytest.raises(EventStoreIntegrityError,match="exact blocker/definition route"):
        dabl.plan_append(both,expected_previous_head=both[-1].event_hash,event_id="DABL-EVT-2026-000004",event_type="evidence_gap_packet",occurred_at=NOW,payload=missing.to_dict())


def _tier_a_readiness_records():
    records=valid_records();asset=version()
    requirement=dabl.IndependentReplayRequirement("RPL-2026-001",asset.asset_version_id,H,"independent-reviewer",NOW,None,None)
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000006",event_type="replay_requirement",occurred_at=NOW,payload=requirement.to_dict())
    replay=dabl.IndependentReplayReceipt("RRC-2026-001",requirement.replay_id,asset.asset_version_id,H,H,H,H,H,H,records[-1].event_hash,"producer","independent-reviewer",NOW)
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000007",event_type="independent_replay_receipt",occurred_at=NOW,payload=replay.to_dict())
    certification=dabl.DataCertification("DCE-2026-001",asset.asset_version_id,dabl.DataTier.A,dabl.CertificationStatus.DRAFT_UNVERIFIED,"certifier",None,NOW,ref(),H,requirement,canonical_hash(replay.to_dict()),NOW+dt.timedelta(days=1),"draft")
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000008",event_type="certification",occurred_at=NOW,payload=certification.to_dict())
    packet=dabl.DataReadinessPacket("DRP-2026-001",dabl.AuthorityState.DRAFT_NONCANONICAL_PHASE1_BLOCKED,ref(),("DAD-2026-001",),("DCE-2026-001",),dabl.DataTier.A,dabl.NextAction.PREPARE_OWNER_REVIEW,NOW)
    return dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000009",event_type="readiness_packet",occurred_at=NOW,payload=packet.to_dict())


def test_terminal_revocation_cannot_be_reset_to_draft_status():
    records=_tier_a_readiness_records();asset_id=version().asset_version_id
    revocation=dabl.RevocationRecord("REV-2026-001",asset_id,"retracted",NOW,H)
    records=dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000010",event_type="revocation",occurred_at=NOW,payload=revocation.to_dict())
    reset=dabl.DataStatusRecord("DST-2026-001",asset_id,dabl.CertificationStatus.DRAFT_UNVERIFIED,NOW,"attempted reset",None,None)
    with pytest.raises(EventStoreIntegrityError,match="status binding"):
        dabl.plan_append(records,expected_previous_head=records[-1].event_hash,event_id="DABL-EVT-2026-000011",event_type="status",occurred_at=NOW,payload=reset.to_dict())


def test_stale_and_blocked_status_invalidate_packet_with_exact_current_maps():
    ready=_tier_a_readiness_records();asset_id=version().asset_version_id
    stale_at=NOW+dt.timedelta(days=1,seconds=1)
    stale_records=dabl.plan_append(ready,expected_previous_head=ready[-1].event_hash,event_id="DABL-EVT-2026-000010",event_type="source_registration",occurred_at=stale_at,payload=dabl.SourceRegistration("SRC-2026-002","synthetic-2","synthetic-2","owner",H,stale_at).to_dict())
    stale=dabl.project_event_plan(stale_records).to_dict()
    assert {key:stale[key] for key in ("asset_current_statuses","certification_current_statuses","packet_current_validity","packet_current_reasons")}=={
        "asset_current_statuses":{asset_id:"DRAFT_UNVERIFIED"},
        "certification_current_statuses":{"DCE-2026-001":"DRAFT_UNVERIFIED"},
        "packet_current_validity":{"DRP-2026-001":False},
        "packet_current_reasons":{"DRP-2026-001":"CERTIFICATION_STALE"},
    }

    blocked_at=NOW+dt.timedelta(hours=1)
    blocked_record=dabl.DataStatusRecord("DST-2026-001",asset_id,dabl.CertificationStatus.BLOCKED,blocked_at,"freshness evidence blocked",None,None)
    blocked_records=dabl.plan_append(ready,expected_previous_head=ready[-1].event_hash,event_id="DABL-EVT-2026-000010",event_type="status",occurred_at=blocked_at,payload=blocked_record.to_dict())
    blocked=dabl.project_event_plan(blocked_records).to_dict()
    assert {key:blocked[key] for key in ("asset_current_statuses","certification_current_statuses","packet_current_validity","packet_current_reasons")}=={
        "asset_current_statuses":{asset_id:"BLOCKED"},
        "certification_current_statuses":{"DCE-2026-001":"BLOCKED"},
        "packet_current_validity":{"DRP-2026-001":False},
        "packet_current_reasons":{"DRP-2026-001":"CERTIFICATION_STATUS_BLOCKED"},
    }


def test_manifest_projection_metadata_pin_and_docs_are_exact():
    projection=dabl.project_event_plan(()).to_dict()
    assert tuple(dabl.SCHEMA_MANIFEST_V2["projection_fields"])==tuple(projection)
    assert len(dabl.SCHEMA_MANIFEST_V2["projection_fields"])==len(set(dabl.SCHEMA_MANIFEST_V2["projection_fields"]))
    event_fields=tuple(item["name"] for item in dabl.SCHEMA_MANIFEST_V2["dabl_event_field_metadata"])
    projection_fields=tuple(item["name"] for item in dabl.SCHEMA_MANIFEST_V2["projection_field_metadata"])
    assert event_fields==tuple(dabl.SCHEMA_MANIFEST_V2["dabl_event_fields"])
    assert projection_fields==tuple(dabl.SCHEMA_MANIFEST_V2["projection_fields"])
    assert event_fields==tuple(dabl.SCHEMA_MANIFEST_V2["literal_contract_fields"]["DABLEvent"])
    assert projection_fields==tuple(dabl.SCHEMA_MANIFEST_V2["literal_contract_fields"]["DABLProjection"])
    assert tuple(dabl.SCHEMA_MANIFEST_V2["contract_field_metadata"]["DABLEvent"])==tuple(dabl.SCHEMA_MANIFEST_V2["dabl_event_field_metadata"])
    assert tuple(dabl.SCHEMA_MANIFEST_V2["contract_field_metadata"]["DABLProjection"])==tuple(dabl.SCHEMA_MANIFEST_V2["projection_field_metadata"])
    assert all(set(item)=={"name","type","required","default"} and item["required"] is True for item in dabl.SCHEMA_MANIFEST_V2["dabl_event_field_metadata"])
    assert all(set(item)=={"name","type","required","default"} and item["required"] is True for item in dabl.SCHEMA_MANIFEST_V2["projection_field_metadata"])
    fields=dabl.SCHEMA_MANIFEST_V2["contract_field_metadata"]["IndependentReplayRequirement"]
    assert tuple(field["name"] for field in fields)==tuple(dabl.SCHEMA_MANIFEST_V2["literal_contract_fields"]["IndependentReplayRequirement"])
    requested=next(field for field in fields if field["name"]=="requested_at")
    assert requested["type"]=="datetime" and requested["required"] is True
    assert dabl.REVIEWED_SCHEMA_MANIFEST_SHA256=="a70874492b8bc58f558e3694eb1af3d146aa629c6623877823e4bb738d661429"
    assert dabl.SCHEMA_MANIFEST_SHA256==dabl.REVIEWED_SCHEMA_MANIFEST_SHA256 and dabl.verify_schema_manifest()
    package=Path(dabl.__file__).resolve().parent
    for document in (package/"README.md",package/"QS_004_DRAFT_OWNER_PACKET.md"):
        text=document.read_text(encoding="utf-8")
        assert dabl.REVIEWED_SCHEMA_MANIFEST_SHA256 in text
        assert "DRAFT_NONCANONICAL" in text and "PHASE1_BLOCKED" in text
        assert "not proof" in text
