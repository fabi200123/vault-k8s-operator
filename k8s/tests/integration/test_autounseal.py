from pathlib import Path

import pytest
from juju.application import Application
from pytest_operator.plugin import OpsTest

from tests.integration.config import (
    APPLICATION_NAME,
    AUTOUNSEAL_TOKEN_SECRET_LABEL,
    JUJU_FAST_INTERVAL,
    METADATA,
    NUM_VAULT_UNITS,
)
from tests.integration.helpers import (
    authorize_charm,
    crash_pod,
    deploy_vault_and_wait,
    get_leader_unit,
    get_model_secret_field,
    get_unit_address,
    initialize_unseal_authorize_vault,
    initialize_vault_leader,
    revoke_token,
    wait_for_status_message,
)
from tests.integration.vault import Vault

root_token = ""
root_token_vault_b = ""
unseal_key = ""
recovery_key_vault_b = ""


@pytest.mark.skip_if_deployed
@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, vault_charm_path: Path):
    """Build and deploy the application."""
    global root_token, unseal_key
    assert ops_test.model
    resources = {"vault-image": METADATA["resources"]["vault-image"]["upstream-source"]}
    await ops_test.model.deploy(
        vault_charm_path,
        resources=resources,
        application_name="vault-b",
        trust=True,
        series="noble",
        num_units=1,
        config={"common_name": "example.com"},
    )
    await deploy_vault_and_wait(
        ops_test,
        charm_path=vault_charm_path,
        num_units=NUM_VAULT_UNITS,
        status="blocked",
    )

    await ops_test.model.wait_for_idle(
        apps=["vault-b"],
        status="blocked",
        timeout=600,
        wait_for_exact_units=1,
    )

    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME],
        status="blocked",
        timeout=600,
        wait_for_exact_units=NUM_VAULT_UNITS,
    )
    root_token, unseal_key = await initialize_unseal_authorize_vault(ops_test, APPLICATION_NAME)


@pytest.mark.abort_on_fail
async def test_given_vault_is_deployed_when_integrate_another_vault_then_autounseal_activated(
    ops_test: OpsTest,
):
    assert ops_test.model
    global root_token_vault_b, recovery_key_vault_b

    await ops_test.model.integrate(
        f"{APPLICATION_NAME}:vault-autounseal-provides", "vault-b:vault-autounseal-requires"
    )
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=["vault-b"], status="blocked", wait_for_exact_units=1, idle_period=5
        )

        await wait_for_status_message(
            ops_test=ops_test,
            expected_message="Please initialize Vault",
            app_name="vault-b",
        )

        root_token_vault_b, recovery_key_vault_b = await initialize_vault_leader(ops_test, "vault-b")
        await wait_for_status_message(
            ops_test=ops_test,
            expected_message="Please authorize charm (see `authorize-charm` action)",
            app_name="vault-b",
        )
        await authorize_charm(ops_test, root_token_vault_b, "vault-b")
        await ops_test.model.wait_for_idle(
            apps=["vault-b"],
            status="active",
            wait_for_exact_units=1,
            idle_period=5,
        )


@pytest.mark.abort_on_fail
async def test_given_vault_b_is_deployed_and_unsealed_when_scale_up_then_status_is_active(
    ops_test: OpsTest,
):
    assert ops_test.model

    app = ops_test.model.applications["vault-b"]
    assert isinstance(app, Application)
    await app.scale(1)
    await ops_test.model.wait_for_idle(
        apps=["vault-b"],
        status="active",
        wait_for_exact_units=1,
        idle_period=5,
    )
    await app.scale(3)
    await ops_test.model.wait_for_idle(
        apps=["vault-b"],
        status="active",
        wait_for_exact_units=3,
        idle_period=5,
    )


@pytest.mark.abort_on_fail
async def test_given_vault_b_is_deployed_and_unsealed_when_all_units_crash_then_units_recover(
    ops_test: OpsTest,
):
    assert ops_test.model

    app = ops_test.model.applications["vault-b"]
    assert isinstance(app, Application)
    await ops_test.model.wait_for_idle(
        apps=["vault-b"],
        status="active",
        wait_for_exact_units=3,
        idle_period=5,
    )
    k8s_namespace = ops_test.model.name
    crash_pod(name="vault-b-0", namespace=k8s_namespace)
    crash_pod(name="vault-b-1", namespace=k8s_namespace)
    crash_pod(name="vault-b-2", namespace=k8s_namespace)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=["vault-b"],
            status="active",
            wait_for_exact_units=3,
            idle_period=5,
        )
        leader_unit = await get_leader_unit(ops_test.model, "vault-b")
    leader_unit_address = await get_unit_address(ops_test=ops_test, unit_name=leader_unit.name)
    vault = Vault(
        url=f"https://{leader_unit_address}:8200",
        token=root_token_vault_b,
    )
    await vault.wait_for_raft_nodes(expected_num_nodes=NUM_VAULT_UNITS)


@pytest.mark.abort_on_fail
async def test_given_vault_b_is_deployed_and_unsealed_when_auth_token_goes_bad_then_units_recover(
    ops_test: OpsTest,
):
    assert ops_test.model

    async with ops_test.fast_forward(fast_interval=JUJU_FAST_INTERVAL):
        await ops_test.model.wait_for_idle(
            apps=["vault-b"],
            status="active",
            wait_for_exact_units=3,
        )
    auth_token = await get_model_secret_field(
        ops_test=ops_test, label=AUTOUNSEAL_TOKEN_SECRET_LABEL, field="token"
    )
    leader_unit = await get_leader_unit(ops_test.model, "vault-b")
    leader_unit_address = await get_unit_address(ops_test=ops_test, unit_name=leader_unit.name)

    revoke_token(
        token_to_revoke=auth_token,
        root_token=root_token_vault_b,
        endpoint=leader_unit_address,
    )
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=["vault-b"],
            status="active",
            wait_for_exact_units=3,
            idle_period=5,
        )
