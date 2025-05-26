import logging
from pathlib import Path

import pytest
from juju.application import Application
from juju.unit import Unit
from pytest_operator.plugin import OpsTest

from tests.integration.config import (
    APPLICATION_NAME,
    JUJU_FAST_INTERVAL,
    NUM_VAULT_UNITS,
    SELF_SIGNED_CERTIFICATES_APPLICATION_NAME,
)
from tests.integration.helpers import (
    authorize_charm_and_wait,
    crash_pod,
    deploy_vault_and_wait,
    get_leader_unit,
    get_unit_address,
    get_unit_status_messages,
    get_vault_ca_certificate,
    initialize_vault_leader,
    unseal_all_vault_units,
    unseal_vault_unit,
    wait_for_status_message,
)
from tests.integration.vault import Vault

logger = logging.getLogger(__name__)


root_key = ""
unseal_key = ""


@pytest.mark.abort_on_fail
async def test_given_vault_deployed_and_initialized_when_unsealed_and_authorized_then_status_is_active(
    ops_test: OpsTest, vault_charm_path: Path
):
    global root_token, unseal_key

    assert ops_test.model
    await deploy_vault_and_wait(ops_test, vault_charm_path, NUM_VAULT_UNITS, status="blocked")
    root_token, unseal_key = await initialize_vault_leader(ops_test, APPLICATION_NAME)
    leader = await get_leader_unit(ops_test.model, APPLICATION_NAME)
    leader_unit_address = await get_unit_address(ops_test, leader.name)
    vault = Vault(
        url=f"https://{leader_unit_address}:8200",
        token=root_token,
    )
    assert vault.is_sealed()
    async with ops_test.fast_forward(fast_interval=JUJU_FAST_INTERVAL):
        await unseal_all_vault_units(ops_test, unseal_key, root_token)
        await authorize_charm_and_wait(ops_test, root_token)
    await vault.wait_for_raft_nodes(expected_num_nodes=NUM_VAULT_UNITS)


@pytest.mark.abort_on_fail
async def test_given_application_is_deployed_when_pod_crashes_then_unit_recovers(
    ops_test: OpsTest,
):
    assert ops_test.model
    crashing_pod_index = 1
    k8s_namespace = ops_test.model.name
    crash_pod(name=f"{APPLICATION_NAME}-1", namespace=k8s_namespace)
    crashed_unit_name = f"{APPLICATION_NAME}/{crashing_pod_index}"
    await wait_for_status_message(
        ops_test,
        expected_message="Please unseal Vault",
        timeout=300,
        unit_name=crashed_unit_name,
    )
    unit_address = await get_unit_address(ops_test, crashed_unit_name)
    vault = Vault(
        url=f"https://{unit_address}:8200",
        token=root_token,
    )
    await unseal_vault_unit(vault, unseal_key)
    async with ops_test.fast_forward(fast_interval=JUJU_FAST_INTERVAL):
        await ops_test.model.wait_for_idle(
            apps=[APPLICATION_NAME],
            status="active",
            timeout=1000,
            wait_for_exact_units=NUM_VAULT_UNITS,
        )


@pytest.mark.abort_on_fail
async def test_given_application_is_deployed_when_scale_up_then_status_is_active(
    ops_test: OpsTest,
):
    assert ops_test.model
    num_units = NUM_VAULT_UNITS + 1
    app: Application = ops_test.model.applications[APPLICATION_NAME]
    await app.scale(num_units)

    await wait_for_status_message(ops_test, expected_message="Please unseal Vault", timeout=300)
    sealed = [
        unit
        for unit, status in await get_unit_status_messages(ops_test)
        if status == "Please unseal Vault"
    ]
    assert len(sealed) == 1
    # TODO: make a function for this to avoid code duplication
    unit_address = await get_unit_address(ops_test, sealed[0])
    vault = Vault(
        url=f"https://{unit_address}:8200",
        token=root_token,
    )
    await unseal_vault_unit(vault, unseal_key)

    async with ops_test.fast_forward(fast_interval=JUJU_FAST_INTERVAL):
        await ops_test.model.wait_for_idle(
            apps=[APPLICATION_NAME],
            status="active",
            timeout=1000,
            wait_for_exact_units=num_units,
        )


@pytest.mark.abort_on_fail
async def test_given_application_is_deployed_when_scale_down_then_status_is_active(
    ops_test: OpsTest,
):
    assert ops_test.model
    app: Application = ops_test.model.applications[APPLICATION_NAME]

    unit_address = await get_unit_address(ops_test, app.units[-1].name)
    vault = Vault(
        url=f"https://{unit_address}:8200",
        token=root_token,
    )

    assert await vault.number_of_raft_nodes() == NUM_VAULT_UNITS + 1

    await app.scale(NUM_VAULT_UNITS)
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME],
        status="active",
        timeout=1000,
        wait_for_exact_units=NUM_VAULT_UNITS,
    )

    unit_address = await get_unit_address(ops_test, app.units[0].name)
    vault = Vault(
        url=f"https://{unit_address}:8200",
        token=root_token,
    )
    assert await vault.number_of_raft_nodes() == NUM_VAULT_UNITS


@pytest.mark.abort_on_fail
async def test_given_vault_deployed_when_tls_access_relation_created_then_existing_certificate_replaced(
    ops_test: OpsTest,
):
    assert ops_test.model

    await ops_test.model.deploy(
        SELF_SIGNED_CERTIFICATES_APPLICATION_NAME,
        application_name=SELF_SIGNED_CERTIFICATES_APPLICATION_NAME,
        channel="1/stable",
        num_units=1,
    )
    async with ops_test.fast_forward(fast_interval=JUJU_FAST_INTERVAL):
        await ops_test.model.wait_for_idle(
            apps=[SELF_SIGNED_CERTIFICATES_APPLICATION_NAME],
            status="active",
            timeout=1000,
        )

    vault_leader_unit = ops_test.model.units[f"{APPLICATION_NAME}/0"]
    assert isinstance(vault_leader_unit, Unit)
    action = await vault_leader_unit.run("cat /var/lib/juju/storage/certs/0/ca.pem")
    await action.wait()
    initial_ca_cert = action.results["stdout"]

    await ops_test.model.integrate(
        relation1=f"{SELF_SIGNED_CERTIFICATES_APPLICATION_NAME}:certificates",
        relation2=f"{APPLICATION_NAME}:tls-certificates-access",
    )

    await ops_test.model.wait_for_idle(
        apps=[SELF_SIGNED_CERTIFICATES_APPLICATION_NAME],
        status="active",
        timeout=1000,
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME],
        status="blocked",
        timeout=1000,
    )

    final_ca_cert = await get_vault_ca_certificate(vault_leader_unit)
    assert initial_ca_cert != final_ca_cert

    await unseal_all_vault_units(ops_test, unseal_key, root_token)

    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[APPLICATION_NAME],
            status="active",
            timeout=1000,
        )


@pytest.mark.abort_on_fail
async def test_given_vault_deployed_when_tls_access_relation_destroyed_then_self_signed_cert_created(
    ops_test: OpsTest,
):
    assert ops_test.model

    vault_leader_unit = ops_test.model.units[f"{APPLICATION_NAME}/0"]
    assert isinstance(vault_leader_unit, Unit)
    action = await vault_leader_unit.run("cat /var/lib/juju/storage/certs/0/ca.pem")
    await action.wait()
    initial_ca_cert = action.results

    app = ops_test.model.applications[APPLICATION_NAME]
    assert isinstance(app, Application)
    await app.remove_relation(
        "tls-certificates-access", f"{SELF_SIGNED_CERTIFICATES_APPLICATION_NAME}:certificates"
    )
    await ops_test.model.wait_for_idle(
        apps=[SELF_SIGNED_CERTIFICATES_APPLICATION_NAME],
        status="active",
        timeout=1000,
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME],
        status="blocked",
        timeout=1000,
    )

    final_ca_cert = await get_vault_ca_certificate(vault_leader_unit)
    assert initial_ca_cert != final_ca_cert

    await unseal_all_vault_units(ops_test, unseal_key, root_token)

    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[APPLICATION_NAME],
            status="active",
            timeout=1000,
        )
