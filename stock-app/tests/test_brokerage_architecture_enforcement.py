"""Structural enforcement of the brokerage and provider-transport boundaries.

Behaviour is characterized elsewhere. What is asserted here is *ownership*: which
module is allowed to import what, and where provider-specific knowledge may live.
These are source scans, so a violation is caught the moment it is written rather
than when a future provider swap breaks a materializer.

The rules, in the order the cleanup plan states them:

* a provider SDK is imported only under ``services/``;
* quote, Greek/IV, and market-metric transport is reached only through
  ``services.options_market``;
* read adapters consume artifacts and never a service or a provider;
* brokerage importers carry no market-data transport and no provider symbol
  syntax;
* ``options_activity`` uses ``services.tastytrade`` for its brokerage-account
  role only;
* utilities quote enrichment routes through the neutral API while Yahoo chain
  discovery stays separate;
* a holdings command materializes holdings and nothing else; and
* ``retirement_options`` is gone from production.

No test here imports a provider SDK, reads a credential, or opens a socket.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

APP = REPO_ROOT / "stock-app" / "app"
UTILITIES = REPO_ROOT / "utilities"
SERVICES = REPO_ROOT / "services"
TOOLS = REPO_ROOT / "tools"
ADAPTERS = APP / "brokerages" / "adapters"
IMPORTERS = APP / "brokerages" / "importers"
MARKET_DATA_PROVIDERS = SERVICES / "options_market" / "providers"

#: Raw provider transport for live market data. Reaching any of these outside a
#: provider package means a caller learned the provider's name again.
MARKET_DATA_TRANSPORT = frozenset({
    "fetch_quotes", "fetch_quotes_async", "fetch_greeks", "fetch_market_metrics",
})

#: The provider SDK distributions. Their transports live under ``services/``.
PROVIDER_SDKS = ("tastytrade", "snaptrade_client")

SKIPPED_DIRECTORIES = {".venv", "__pycache__", "node_modules", ".git"}


# --------------------------------------------------------------------------- #
# source-scanning helpers                                                      #
# --------------------------------------------------------------------------- #

def _is_test_source(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_")


def production_sources(*roots: Path) -> list[Path]:
    """Every production ``.py`` under ``roots``; tests are consumers, not owners."""
    found: list[Path] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if any(part in SKIPPED_DIRECTORIES for part in path.parts):
                continue
            if _is_test_source(path):
                continue
            found.append(path)
    return found


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def imported_modules(tree: ast.Module) -> set[str]:
    """Absolute module paths ``tree`` imports, including ``from`` targets."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def imports_package(tree: ast.Module, package: str) -> bool:
    return any(module == package or module.startswith(f"{package}.")
               for module in imported_modules(tree))


def names_bound_to(tree: ast.Module, package: str) -> set[str]:
    """Local names that refer to ``package`` or one of its submodules."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == package or alias.name.startswith(f"{package}."):
                    bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if (node.module == package or node.module.startswith(f"{package}.")
                        or full == package or full.startswith(f"{package}.")):
                    bound.add(alias.asname or alias.name)
    return bound


def attributes_used_on(tree: ast.AST, names: set[str]) -> set[str]:
    """Attribute names read from any of ``names`` — ``io.fetch_quotes`` -> that."""
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in names
    }


def names_imported_from(tree: ast.Module, package: str) -> set[str]:
    """Names imported *out of* ``package``, e.g. ``from ...io import fetch_quotes``."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 0 or not node.module:
            continue
        if node.module == package or node.module.startswith(f"{package}."):
            imported.update(alias.name for alias in node.names)
    return imported


def function_named(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined")


def called_names(node: ast.AST) -> set[str]:
    """Every plain and attribute call target inside ``node``."""
    calls: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            calls.add(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            calls.add(child.func.attr)
    return calls


# --------------------------------------------------------------------------- #
# provider SDK confinement                                                     #
# --------------------------------------------------------------------------- #

def test_provider_sdks_are_imported_only_under_services():
    """`utilities/tests/test_brokerages.py` asserts the same rule from the other
    runtime. Both suites must fail if a provider SDK escapes the transport."""
    for path in production_sources(APP, UTILITIES, TOOLS, REPO_ROOT / "studies",
                                   REPO_ROOT / "models"):
        modules = imported_modules(parse(path))
        offenders = sorted(
            module for module in modules
            for sdk in PROVIDER_SDKS
            if module == sdk or module.startswith(f"{sdk}.")
        )
        assert offenders == [], f"{path} imports a provider SDK: {offenders}"


def test_services_imports_a_provider_sdk_lazily_inside_a_function():
    """Importing ``services`` must never require an installed provider SDK: both
    runtimes import it, and a missing optional dependency is a capability state."""
    for path in production_sources(SERVICES):
        tree = parse(path)
        inside_functions = {
            id(node)
            for function in ast.walk(tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            for node in ast.walk(function)
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            # A relative import cannot reach a distribution; ``from .providers
            # import tastytrade`` is this package's own adapter module.
            if getattr(node, "level", 0):
                continue
            module = getattr(node, "module", "") or ""
            targets = [module, *(alias.name for alias in node.names)]
            if not any(target == sdk or target.startswith(f"{sdk}.")
                       for target in targets for sdk in PROVIDER_SDKS):
                continue
            assert id(node) in inside_functions, (
                f"{path}:{node.lineno} imports a provider SDK at module scope")


# --------------------------------------------------------------------------- #
# market-data transport routes through the neutral API                         #
# --------------------------------------------------------------------------- #

def test_market_data_transport_is_reached_only_through_the_neutral_api():
    """Quotes, Greeks/IV, and market metrics are provider-neutral reads. Only the
    ``services.options_market`` adapter may name the provider that serves them."""
    for path in production_sources(APP, UTILITIES):
        tree = parse(path)
        provider_names = names_bound_to(tree, "services.tastytrade")
        used = attributes_used_on(tree, provider_names) & MARKET_DATA_TRANSPORT
        used |= names_imported_from(tree, "services.tastytrade") & MARKET_DATA_TRANSPORT
        assert used == set(), (
            f"{path} calls Tastytrade market-data transport {sorted(used)} directly; "
            "route it through services.options_market")


def test_the_neutral_api_exposes_every_market_data_read_its_callers_need():
    from services import options_market

    for entry_point in ("fetch_quotes", "fetch_quotes_async", "fetch_greeks",
                        "fetch_underlying_metrics"):
        assert callable(getattr(options_market, entry_point))


# --------------------------------------------------------------------------- #
# read adapters                                                                #
# --------------------------------------------------------------------------- #

def test_read_adapters_never_import_a_service_or_a_provider():
    """An adapter turns materialized rows into canonical facts. Reaching a
    provider from there would make a read path depend on credentials."""
    adapters = production_sources(ADAPTERS)
    assert adapters, "no adapter sources found"
    for path in adapters:
        modules = sorted(m for m in imported_modules(parse(path))
                         if m == "services" or m.startswith("services."))
        assert modules == [], f"{path} imports {modules}"


def test_read_adapters_call_no_provider_transport():
    for path in production_sources(ADAPTERS):
        called = called_names(parse(path))
        assert called & MARKET_DATA_TRANSPORT == set(), path


# --------------------------------------------------------------------------- #
# brokerage importers                                                          #
# --------------------------------------------------------------------------- #

def test_held_option_market_data_importer_holds_no_provider_transport():
    tree = parse(IMPORTERS / "held_option_market_data.py")
    assert imports_package(tree, "services.options_market")
    assert not imports_package(tree, "services.tastytrade")
    assert not imports_package(tree, "services.snaptrade")


def test_snaptrade_importer_uses_account_transport_only():
    """Deliberate deviation from a literal reading of "no provider transport in
    an importer": SnapTrade *is* how the Fidelity account is read, so the
    importer keeps that account transport, exactly as ``options_activity`` keeps
    Tastytrade's. What it may not have is market-data transport or provider
    symbol syntax."""
    tree = parse(IMPORTERS / "snaptrade.py")
    assert imports_package(tree, "services.snaptrade")
    assert not imports_package(tree, "services.tastytrade")
    assert not imports_package(tree, "services.options_market")


@pytest.mark.parametrize("module", ["snaptrade.py", "held_option_market_data.py"])
def test_importers_carry_no_provider_symbol_syntax(module):
    tree = parse(IMPORTERS / module)
    assert not imports_package(tree, "services.options_market.providers")
    assert _dxfeed_converters(tree) == []


# --------------------------------------------------------------------------- #
# options_activity: brokerage account role vs market-data role                 #
# --------------------------------------------------------------------------- #

#: Tastytrade's *brokerage account* surface. Anything else on that transport is
#: the market-data role, which belongs to the neutral API.
ACCOUNT_TRANSPORT = frozenset({"fetch_account_data", "TastytradeConfigurationError"})


def test_options_activity_uses_tastytrade_only_for_its_account_role():
    tree = parse(APP / "options_activity.py")
    used = attributes_used_on(tree, names_bound_to(tree, "services.tastytrade"))
    assert used <= ACCOUNT_TRANSPORT, sorted(used - ACCOUNT_TRANSPORT)


def test_options_activity_reads_market_data_through_the_neutral_api():
    tree = parse(APP / "options_activity.py")
    assert imports_package(tree, "services.options_market")
    market_data = attributes_used_on(tree, names_bound_to(tree, "services.options_market"))
    assert {"fetch_greeks", "fetch_underlying_metrics"} <= market_data


# --------------------------------------------------------------------------- #
# utilities: quote enrichment vs Yahoo chain discovery                         #
# --------------------------------------------------------------------------- #

def test_utilities_quote_enrichment_routes_through_the_neutral_api():
    tree = parse(UTILITIES / "options" / "market_quotes.py")
    assert imports_package(tree, "services.options_market")
    assert not imports_package(tree, "services.tastytrade")
    assert not imports_package(tree, "services.options_market.providers")


def test_yahoo_chain_discovery_stays_separate_from_the_market_data_provider():
    """Chain discovery is still Yahoo's and is deliberately not part of the
    neutral options API. Its only provider-adapter dependency is the streamer
    identity it records for diagnostics — the conversion itself stays in the
    adapter."""
    tree = parse(UTILITIES / "options" / "chains.py")
    assert not imports_package(tree, "services.tastytrade")
    assert names_imported_from(tree, "services.options_market.providers") <= {
        "occ_to_dxfeed_symbol"}
    assert _dxfeed_converters(tree) == []


# --------------------------------------------------------------------------- #
# provider symbol syntax is defined once                                       #
# --------------------------------------------------------------------------- #

#: A dxFeed streamer symbol is a dot-prefixed interpolation of OCC parts. A
#: function that builds one *and* pulls an OCC strike out of a match is a
#: converter, wherever it happens to be defined.
_DXFEED_LITERAL = re.compile(r"f['\"]\.\{")
_OCC_STRIKE_GROUP = re.compile(r"group\(['\"]strike['\"]\)")


def _dxfeed_converters(tree: ast.Module) -> list[str]:
    converters = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        source = ast.unparse(node)
        if _DXFEED_LITERAL.search(source) and _OCC_STRIKE_GROUP.search(source):
            converters.append(node.name)
        elif "dxfeed" in node.name.lower() or "streamer_symbol" in node.name.lower():
            converters.append(node.name)
    return converters


def test_occ_to_dxfeed_conversion_is_defined_only_in_the_provider_adapter():
    """One definition, in the adapter that owns the provider's symbol syntax.
    Callers that need the streamer identity import it from there rather than
    re-deriving it, so a provider swap edits one function."""
    defining: dict[Path, list[str]] = {}
    for path in production_sources(APP, UTILITIES, SERVICES, TOOLS):
        converters = _dxfeed_converters(parse(path))
        if converters:
            defining[path] = converters
    assert set(defining) == {MARKET_DATA_PROVIDERS / "tastytrade.py"}, {
        str(path.relative_to(REPO_ROOT)): names for path, names in defining.items()}


# --------------------------------------------------------------------------- #
# single-purpose resource commands                                             #
# --------------------------------------------------------------------------- #

def test_fidelity_resource_commands_are_the_owning_modules_themselves():
    """Registry fidelity: HOLDINGS is the holdings importer, not the legacy
    all-resource orchestrator that also fetches activity and market data."""
    from app import snaptrade_service
    from app.brokerages import registry
    from app.brokerages.importers import held_option_market_data
    from app.brokerages.importers import snaptrade as snaptrade_importer

    commands = registry.REGISTRY["fidelity"].sync_commands
    assert commands["HOLDINGS"] is snaptrade_importer.sync_holdings
    assert commands["HOLDINGS"] is not snaptrade_service.sync
    assert commands["MARKET_DATA"] is (
        held_option_market_data.sync_held_option_market_data)
    assert len({id(command) for command in commands.values()}) == 3


def test_holdings_materialization_calls_no_sibling_resource():
    tree = parse(IMPORTERS / "snaptrade.py")
    called = called_names(function_named(tree, "sync_holdings"))
    siblings = {"sync_activity", "sync_events", "sync_market_data", "sync_betas",
                "sync_greeks", "sync_held_option_market_data", "fetch_activities"}
    assert called & siblings == set(), sorted(called & siblings)


# --------------------------------------------------------------------------- #
# retired module                                                               #
# --------------------------------------------------------------------------- #

def test_no_production_module_references_retirement_options():
    for path in production_sources(APP, TOOLS, SERVICES, UTILITIES):
        assert "retirement_options" not in path.read_text(encoding="utf-8"), path
    assert not (APP / "retirement_options.py").exists()
