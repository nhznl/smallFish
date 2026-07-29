"""One conformance suite, run against every registered adapter.

The point of the registry is that a projection never learns which brokerage it
is reading. That only holds if both adapters really do produce the same shapes
and the same signs from artifacts that look nothing alike — so the assertions
here are written once and parametrized over the registry, rather than as two
sets of provider-specific expectations.

Every scenario is the *same* economic position expressed in each provider's
artifact format: 100 long shares, one short put opened for a 600 credit, marked
at 0.75. If the adapters disagree, these tests disagree.
"""

from __future__ import annotations

import csv
from decimal import Decimal

import pytest

from app import (brokerages, config, options_activity, retirement_options,
                 snaptrade_service)
from app.brokerages import contracts, registry
from app.brokerages.adapters.base import BrokerageAdapter

CONTRACT = "ABC   260821P00050000"
BROKERAGE_IDS = sorted(registry.REGISTRY)


# --------------------------------------------------------------- artifacts ---

@pytest.fixture
def adapter_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    for name, filename in (
        ("SFP_TASTYTRADE_POSITIONS", "tt_positions.csv"),
        ("SFP_OPTIONS_POSITION_MARKS", "tt_marks.csv"),
        ("SFP_OPTIONS_ACTIVITY", "tt_activity.csv"),
        ("SFP_OPTIONS_GROUPS", "groups.csv"),
        ("SFP_OPTIONS_GROUP_MEMBERS", "members.csv"),
        ("SFP_OPTIONS_GREEKS", "tt_greeks.csv"),
        ("SFP_OPTIONS_BETAS", "tt_betas.csv"),
        ("SFP_SNAPTRADE_HOLDINGS", "st_holdings.csv"),
        ("SFP_RETIREMENT_OPTION_EVENTS", "st_events.csv"),
        ("SFP_RETIREMENT_OPTION_GREEKS", "st_greeks.csv"),
        ("SFP_RETIREMENT_OPTION_BETAS", "st_betas.csv"),
    ):
        monkeypatch.setenv(name, str(tmp_path / filename))
    return tmp_path


def _write_tastytrade(*, positions=(), activity=(), greeks=(), betas=()) -> None:
    def position(**kw):
        row = {header: "" for header in options_activity.COMBINED_POSITION_HEADERS}
        row.update({"schema_version": "1", "source": "TASTYTRADE",
                    "account": "TRADING", "retrieved_at": "2026-07-28T16:01:00+00:00",
                    "updated_at": "2026-07-28T16:00:00+00:00"})
        row.update(kw)
        row["contract_key"] = options_activity._contract_key(row["contract_symbol"])
        return row

    def event(**kw):
        row = {header: "" for header in options_activity.ACTIVITY_HEADERS}
        row.update({"schema_version": "1", "source": "TASTYTRADE",
                    "account": "TRADING", "retrieved_at": "2026-07-28T16:01:00+00:00",
                    "imported_at": "2026-07-28T16:01:00+00:00"})
        row.update(kw)
        row["contract_key"] = options_activity._contract_key(row["contract_symbol"])
        return row

    options_activity._atomic_write(
        config.tastytrade_positions_csv(), options_activity.COMBINED_POSITION_HEADERS,
        [position(**row) for row in positions],
    )
    options_activity._atomic_write(
        config.options_activity_csv(), options_activity.ACTIVITY_HEADERS,
        [event(**row) for row in activity],
    )
    options_activity._atomic_write(
        config.options_greeks_csv(), options_activity.GREEKS_HEADERS, list(greeks)
    )
    options_activity._atomic_write(
        config.options_betas_csv(), options_activity.BETA_HEADERS, list(betas)
    )


def _write_snaptrade(*, holdings=(), events=(), greeks=(), betas=()) -> None:
    def holding(**kw):
        row = {header: "" for header in snaptrade_service.HOLDINGS_HEADERS}
        row.update({"schema_version": "1", "source": "SNAPTRADE",
                    "account_id": "acct-1", "account_name": "BrokerageLink",
                    "institution": "Fidelity", "currency": "USD",
                    "retrieved_at": "2026-07-28T16:02:00+00:00",
                    "imported_at": "2026-07-28T16:02:01+00:00"})
        row.update(kw)
        return row

    def event(**kw):
        row = {header: "" for header in retirement_options.EVENT_HEADERS}
        row.update({"schema_version": "1", "source": "SNAPTRADE",
                    "account_id": "acct-1", "account": "BrokerageLink",
                    "activity_type": "TRADE",
                    "retrieved_at": "2026-07-28T16:02:00+00:00",
                    "imported_at": "2026-07-28T16:02:00+00:00"})
        row.update(kw)
        return row

    path = config.snaptrade_holdings_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=snaptrade_service.HOLDINGS_HEADERS)
        writer.writeheader()
        writer.writerows(holding(**row) for row in holdings)
    retirement_options._atomic_write(
        config.retirement_option_events_csv(), retirement_options.EVENT_HEADERS,
        [event(**row) for row in events],
    )
    retirement_options._atomic_write(
        config.retirement_option_greeks_csv(), retirement_options.GREEKS_HEADERS,
        list(greeks),
    )
    retirement_options._atomic_write(
        config.retirement_option_betas_csv(), retirement_options.BETA_HEADERS,
        list(betas),
    )


def write_covered_put(brokerage_id: str) -> None:
    """The same economic position, in whichever artifact family it belongs to.

    100 shares at a 110 cost and a 120 mark; one short put opened for a 600
    credit and marked at 0.75.
    """
    if brokerage_id == "tastytrade":
        _write_tastytrade(
            positions=[
                {"instrument_type": "Equity", "contract_symbol": "ABC",
                 "underlying_symbol": "ABC", "quantity": "100", "direction": "Long",
                 "signed_quantity": "100", "multiplier": "1", "mark_price": "120",
                 "average_open_price": "110"},
                {"instrument_type": "Equity Option", "contract_symbol": CONTRACT,
                 "underlying_symbol": "ABC", "quantity": "1", "direction": "Short",
                 "signed_quantity": "-1", "multiplier": "100", "mark_price": "0.75",
                 "average_open_price": "6"},
            ],
            activity=[
                {"id": "tastytrade:TRADING:1", "source_transaction_id": "1",
                 "executed_at": "2026-07-01T16:00:00+00:00",
                 "transaction_date": "2026-07-01", "transaction_type": "Trade",
                 "transaction_sub_type": "Sell to Open",
                 "instrument_type": "Equity Option", "contract_symbol": CONTRACT,
                 "underlying_symbol": "ABC", "action": "Sell to Open",
                 "quantity": "1", "position_delta": "-1", "net_value": "600",
                 "fee_effect": "-1", "option_type": "PUT", "expiry": "2026-08-21",
                 "strike": "50"},
            ],
            betas=[{"schema_version": "1", "source": "TASTYTRADE_MARKET_METRICS",
                    "symbol": "ABC", "beta": "1.25",
                    "beta_updated_at": "2026-07-27T17:00:00+00:00",
                    "retrieved_at": "2026-07-28T16:01:00+00:00"}],
        )
    else:
        _write_snaptrade(
            holdings=[
                {"asset_class": "STOCK", "symbol": "ABC", "quantity": "100",
                 "price": "120", "average_purchase_price": "110",
                 "cost_basis": "11000", "market_value": "12000"},
                {"asset_class": "OPTION", "symbol": CONTRACT,
                 "underlying_symbol": "ABC", "option_type": "PUT", "strike": "50",
                 "expiry": "2026-08-21", "quantity": "-1", "price": "0.75",
                 "average_purchase_price": "6", "cost_basis": "-600",
                 "market_value": "-75"},
            ],
            events=[
                {"id": "activity-1", "underlying_symbol": "ABC",
                 "option_type": "PUT", "strike": "50", "expiry": "2026-08-21",
                 "occ_symbol": CONTRACT, "action": "SELL_TO_OPEN", "units": "-1",
                 "net_value": "600", "fee": "-1",
                 "trade_date": "2026-07-01T16:00:00Z"},
            ],
            betas=[{"schema_version": "1", "source": "TASTYTRADE_MARKET_METRICS",
                    "symbol": "ABC", "beta": "1.25",
                    "beta_updated_at": "2026-07-27T17:00:00+00:00",
                    "retrieved_at": "2026-07-28T16:02:00+00:00"}],
        )


def write_empty(brokerage_id: str) -> None:
    if brokerage_id == "tastytrade":
        _write_tastytrade()
    else:
        _write_snaptrade()


# ------------------------------------------------------------- the registry ---

def test_registry_is_the_only_switch_on_brokerage_identity():
    assert sorted(registry.REGISTRY) == ["fidelity", "tastytrade"]
    for brokerage_id, entry in registry.REGISTRY.items():
        assert entry.descriptor.id == brokerage_id
        assert entry.descriptor.adapter in {"SNAPTRADE", "TASTYTRADE"}
    # The public identity and the backend adapter are deliberately different
    # namespaces: `snaptrade` is never a brokerage a user can request.
    assert registry.REGISTRY["fidelity"].descriptor.adapter == "SNAPTRADE"
    with pytest.raises(registry.UnknownBrokerageError) as caught:
        registry.resolve("snaptrade")
    assert caught.value.status_code == 404


def test_registry_lookup_is_case_and_whitespace_insensitive():
    assert registry.resolve("  Fidelity ").descriptor().id == "fidelity"


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_every_adapter_satisfies_the_common_read_interface(brokerage_id):
    adapter = registry.resolve(brokerage_id)
    assert isinstance(adapter, BrokerageAdapter)
    descriptor = adapter.descriptor()
    assert descriptor.id == brokerage_id
    assert descriptor.portfolio_role in {"RETIREMENT", "TRADING"}
    capabilities = adapter.capabilities()
    assert capabilities.holdings and capabilities.options and capabilities.activity


# ------------------------------------------------------- conformance: facts ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_positions_normalize_to_identical_canonical_facts(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    facts = {fact.instrument: fact for fact in registry.resolve(brokerage_id).positions()}
    assert set(facts) == {"EQUITY", "OPTION"}

    equity = facts["EQUITY"]
    assert equity.brokerage_id == brokerage_id
    assert equity.symbol == "ABC"
    assert equity.signed_quantity == Decimal("100")
    assert equity.contract is None
    assert equity.open_cash_flow == Decimal("-11000")   # debit to open a long
    assert equity.market_value == Decimal("12000")
    assert equity.missing == ()

    option = facts["OPTION"]
    assert option.symbol == "ABC"                        # underlying, not the OCC
    assert option.signed_quantity == Decimal("-1")       # short is negative
    assert option.multiplier == Decimal("100")
    assert option.open_cash_flow == Decimal("600")       # credit received
    assert option.market_value == Decimal("-75")         # short is negative
    assert option.mark_per_unit == Decimal("0.75")
    assert option.missing == ()
    assert option.contract.occ_symbol == options_activity._contract_key(CONTRACT)
    assert option.contract.option_type == "PUT"
    assert option.contract.strike == Decimal("50")
    assert option.contract.expiry == "2026-08-21"
    assert option.contract.multiplier == Decimal("100")


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_activity_normalizes_to_identical_canonical_facts(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    facts = registry.resolve(brokerage_id).activity()
    assert len(facts) == 1
    fact = facts[0]

    assert fact.brokerage_id == brokerage_id
    assert fact.provider_event_id                     # stable provider identity
    assert fact.instrument == "OPTION"
    assert fact.symbol == "ABC"
    assert fact.action == "SELL_TO_OPEN"              # one spelling, both providers
    assert fact.position_delta == Decimal("-1")
    assert fact.net_cash_flow == Decimal("600")       # credit is positive
    assert fact.fees == Decimal("-1")
    assert fact.contract.occ_symbol == options_activity._contract_key(CONTRACT)
    assert fact.is_manual is False
    assert fact.missing == ()
    # Provenance names the institution the fact came from. The adapter that
    # read it is a backend detail and must not reach a response.
    descriptor = registry.REGISTRY[brokerage_id].descriptor
    assert fact.provenance.source == descriptor.institution
    if brokerage_id == "fidelity":
        assert fact.provenance.source != descriptor.adapter


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_events_order_by_execution_and_provider_identity(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    fact = registry.resolve(brokerage_id).activity()[0]
    assert fact.order_key == (fact.executed_at, fact.provider_event_id)
    assert fact.executed_at.startswith("2026-07-01")


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_market_observations_carry_timestamped_inputs(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    betas = [
        row for row in registry.resolve(brokerage_id).market_observations()
        if row.beta is not None
    ]
    assert [row.symbol for row in betas] == ["ABC"]
    assert betas[0].beta == Decimal("1.25")
    assert betas[0].observed_at == "2026-07-27T17:00:00+00:00"


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_coverage_never_claims_a_provider_boundary_it_cannot_prove(adapter_env,
                                                                   brokerage_id):
    write_covered_put(brokerage_id)
    coverage = registry.resolve(brokerage_id).coverage()
    assert coverage.history_start == "2026-07-01"
    # The oldest retained event is not proof the provider had nothing earlier.
    assert coverage.reached_provider_boundary is None
    assert contracts.PROVIDER_BOUNDARY_UNKNOWN in coverage.reasons
    assert coverage.equity_activity == "UNAVAILABLE"
    assert coverage.option_activity in contracts.COVERAGE_STATUSES


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_empty_artifacts_are_a_capability_state_not_an_error(adapter_env, brokerage_id):
    write_empty(brokerage_id)
    adapter = registry.resolve(brokerage_id)
    assert adapter.positions() == []
    assert adapter.activity() == []
    coverage = adapter.coverage()
    assert coverage.history_start is None
    assert coverage.option_activity == "UNAVAILABLE"


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_missing_artifacts_do_not_raise(adapter_env, brokerage_id):
    """Nothing synced yet. A missing file is availability, never an exception."""
    adapter = registry.resolve(brokerage_id)
    snapshot = adapter.snapshot()
    assert snapshot.positions == ()
    assert snapshot.activity == ()
    assert snapshot.availability  # explains why, without blocking navigation


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_snapshot_gathers_one_consistent_read(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    snapshot = registry.resolve(brokerage_id).snapshot()
    assert snapshot.descriptor.id == brokerage_id
    assert len(snapshot.positions) == 2
    assert len(snapshot.activity) == 1
    assert snapshot.availability == ()


@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_facts_use_only_the_canonical_vocabulary(adapter_env, brokerage_id):
    write_covered_put(brokerage_id)
    snapshot = registry.resolve(brokerage_id).snapshot()
    for position in snapshot.positions:
        assert position.instrument in contracts.INSTRUMENTS
        assert set(position.missing) <= contracts.MISSING_REASONS
        if position.contract is not None:
            assert position.contract.option_type in contracts.OPTION_TYPES
    for fact in snapshot.activity:
        assert fact.instrument in contracts.INSTRUMENTS
        assert fact.action in contracts.ACTIONS
        assert set(fact.missing) <= contracts.MISSING_REASONS


# ------------------------------------------------- conformance: failing shut ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_a_missing_provider_field_is_null_with_a_reason_never_zero(adapter_env,
                                                                   brokerage_id):
    """An employer-plan fund with no cost basis, and an option with no mark."""
    if brokerage_id == "tastytrade":
        _write_tastytrade(positions=[
            {"instrument_type": "Equity", "contract_symbol": "ABC",
             "underlying_symbol": "ABC", "quantity": "100", "direction": "Long",
             "signed_quantity": "100", "multiplier": "1", "mark_price": "120",
             "average_open_price": ""},
            {"instrument_type": "Equity Option", "contract_symbol": CONTRACT,
             "underlying_symbol": "ABC", "quantity": "1", "direction": "Short",
             "signed_quantity": "-1", "multiplier": "100", "mark_price": "",
             "average_open_price": "6"},
        ])
    else:
        _write_snaptrade(holdings=[
            {"asset_class": "STOCK", "symbol": "ABC", "quantity": "100",
             "price": "120", "cost_basis": "", "market_value": "12000"},
            {"asset_class": "OPTION", "symbol": CONTRACT,
             "underlying_symbol": "ABC", "option_type": "PUT", "strike": "50",
             "expiry": "2026-08-21", "quantity": "-1", "price": "",
             "cost_basis": "-600", "market_value": ""},
        ])

    facts = {fact.instrument: fact for fact in registry.resolve(brokerage_id).positions()}
    equity = facts["EQUITY"]
    assert equity.open_cash_flow is None
    assert contracts.MISSING_OPEN_CASH_FLOW in equity.missing

    option = facts["OPTION"]
    assert option.market_value is None
    assert contracts.MISSING_MARKET_VALUE in option.missing
    assert option.open_cash_flow == Decimal("600")   # what is known stays known


def test_snaptrade_declares_its_unconfirmed_lifecycle_shapes(adapter_env):
    """An assignment has never posted from this provider, so a fact carrying one
    is flagged rather than quietly treated as an ordinary close."""
    _write_snaptrade(events=[
        {"id": "a1", "underlying_symbol": "ABC", "option_type": "PUT",
         "strike": "50", "expiry": "2026-08-21", "occ_symbol": CONTRACT,
         "action": "ASSIGNMENT", "units": "1", "net_value": "0",
         "trade_date": "2026-07-02T16:00:00Z"},
        {"id": "a2", "underlying_symbol": "ABC", "option_type": "PUT",
         "strike": "50", "expiry": "2026-08-21", "occ_symbol": CONTRACT,
         "action": "SOMETHING_NEW", "units": "1", "net_value": "0",
         "trade_date": "2026-07-03T16:00:00Z"},
    ])
    facts = {fact.provider_event_id: fact for fact in registry.resolve("fidelity").activity()}
    assert facts["a1"].action == "ASSIGNMENT"
    assert contracts.UNCONFIRMED_PROVIDER_LIFECYCLE in facts["a1"].missing
    assert facts["a2"].action == "UNKNOWN"
    assert contracts.UNMAPPED_PROVIDER_ACTION in facts["a2"].missing


def test_tastytrade_reads_lifecycle_from_the_receive_deliver_sub_type(adapter_env):
    """A Tastytrade assignment keeps a trade-shaped `action`; the sub-type is the
    fact that identifies it, and the provider supplies no position delta."""
    _write_tastytrade(activity=[
        {"id": "tastytrade:TRADING:3", "source_transaction_id": "3",
         "executed_at": "2026-07-03T16:00:00+00:00", "transaction_date": "2026-07-03",
         "transaction_type": "Receive Deliver", "transaction_sub_type": "Assignment",
         "instrument_type": "Equity Option", "contract_symbol": CONTRACT,
         "underlying_symbol": "ABC", "action": "", "quantity": "1",
         "position_delta": "", "net_value": "0", "option_type": "PUT"},
    ])
    fact = registry.resolve("tastytrade").activity()[0]
    assert fact.action == "ASSIGNMENT"
    assert fact.missing == (contracts.MISSING_POSITION_DELTA,)
    assert fact.position_delta is None
    assert fact.net_cash_flow == Decimal("0")   # a reported zero is a real zero


def test_tastytrade_falls_back_to_the_options_only_position_artifact(adapter_env):
    """Before the combined snapshot exists, open-equity coverage is incomplete
    and the adapter says so instead of reporting an empty brokerage."""
    options_activity._atomic_write(
        config.options_position_marks_csv(), options_activity.MARK_HEADERS,
        [{header: "" for header in options_activity.MARK_HEADERS} | {
            "source": "TASTYTRADE", "account": "TRADING",
            "instrument_type": "Equity Option", "contract_symbol": CONTRACT,
            "contract_key": options_activity._contract_key(CONTRACT),
            "underlying_symbol": "ABC", "quantity": "1", "direction": "Short",
            "signed_quantity": "-1", "multiplier": "100", "mark_price": "0.75",
            "retrieved_at": "2026-07-28T16:01:00+00:00",
        }],
    )
    adapter = registry.resolve("tastytrade")
    position = adapter.positions()[0]
    assert position.market_value == Decimal("-75")
    # The legacy artifact has no average open price, so cost is unavailable.
    assert position.open_cash_flow is None
    assert contracts.MISSING_OPEN_CASH_FLOW in position.missing
    assert any("options-only" in reason for reason in adapter.availability_reasons())


def test_manual_reconciliation_rows_are_identified_as_such(adapter_env):
    _write_tastytrade(activity=[
        {"id": "manual:TRADING:abc", "source": options_activity.MANUAL_SOURCE,
         "executed_at": "2026-07-04T21:00:00+00:00", "transaction_date": "2026-07-04",
         "transaction_type": "Manual Reconciliation",
         "transaction_sub_type": "Pre-window assignment",
         "instrument_type": "Equity Option", "contract_symbol": CONTRACT,
         "underlying_symbol": "ABC", "action": "Manual Adjustment",
         "quantity": "1", "position_delta": "1", "net_value": "0",
         "option_type": "PUT"},
    ])
    fact = registry.resolve("tastytrade").activity()[0]
    assert fact.is_manual is True
    assert fact.action == "MANUAL_ADJUSTMENT"
    assert fact.missing == ()


# ------------------------------------------------------- no provider access ---

@pytest.mark.parametrize("brokerage_id", BROKERAGE_IDS)
def test_read_adapters_never_call_a_provider(adapter_env, brokerage_id, monkeypatch):
    """Reads consume materialized artifacts only. A read path that reached for a
    provider would be a correctness bug, not just a slow request."""
    def forbidden(*args, **kwargs):
        raise AssertionError("a read adapter attempted provider access")

    monkeypatch.setattr(options_activity, "fetch_tastytrade", forbidden)
    monkeypatch.setattr(snaptrade_service, "fetch_activities", forbidden)
    monkeypatch.setattr(snaptrade_service, "sync", forbidden)
    write_covered_put(brokerage_id)
    registry.resolve(brokerage_id).snapshot()


def test_public_package_exports_the_registry_entry_points():
    assert brokerages.resolve("tastytrade").descriptor().institution == "TASTYTRADE"
    assert brokerages.brokerage_ids() == list(registry.REGISTRY)
    assert [entry.id for entry in brokerages.descriptors()] == list(registry.REGISTRY)


# ------------------------------------------------ the switch stays in one place ---

_FORBIDDEN_IDENTIFIERS = ("fidelity", "tastytrade", "snaptrade", "retirement", "trading")


def _code_tokens(path):
    """Identifiers and non-docstring string literals in one module."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    tokens = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.append(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.append(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tokens.append(node.name)
        elif isinstance(node, ast.alias):
            tokens.append(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                tokens.append(node.value)
    return tokens


def test_only_the_registry_names_a_brokerage():
    """Phase 2's exit criterion, enforced rather than reviewed.

    Common code may not contain an `if fidelity` / `if trading` transformation
    branch. Provider vocabulary belongs in `adapters/`, and identity selection
    belongs in `registry.py`; every other module in the package must work purely
    from the descriptor it is handed.
    """
    from pathlib import Path

    import app.brokerages as package

    root = Path(package.__file__).resolve().parent
    common = [
        path for path in sorted(root.rglob("*.py"))
        if path.name != "registry.py" and path.parent.name != "adapters"
    ]
    assert {path.name for path in common} >= {"contracts.py", "__init__.py"}

    offenders = {
        f"{path.name}:{token}"
        for path in common
        for token in _code_tokens(path)
        if any(word in token.lower() for word in _FORBIDDEN_IDENTIFIERS)
    }
    assert offenders == set()
