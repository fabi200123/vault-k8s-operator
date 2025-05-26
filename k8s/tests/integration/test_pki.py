import asyncio
from pathlib import Path

import pytest
from juju.action import Action
from juju.unit import Unit
from pytest_operator.plugin import OpsTest

from tests.integration.config import (
    APPLICATION_NAME,
    NUM_VAULT_UNITS,
    SELF_SIGNED_CERTIFICATES_APPLICATION_NAME,
    VAULT_PKI_REQUIRER_APPLICATION_NAME,
    VAULT_PKI_REQUIRER_REVISION,
)
from tests.integration.helpers import (
    deploy_vault_and_wait,
    get_leader_unit,
    get_unit_address,
    get_vault_pki_intermediate_ca_common_name,
    initialize_unseal_authorize_vault,
    wait_for_status_message,
)

root_token = ""
unseal_key = ""


@pytest.mark.abort_on_fail
async def test_given_tls_certificates_pki_relation_when_integrate_then_status_is_active(
    ops_test: OpsTest, vault_charm_path: Path
):
    assert ops_test.model

    deploy_self_signed_certificates = ops_test.model.deploy(
        SELF_SIGNED_CERTIFICATES_APPLICATION_NAME,
        application_name=SELF_SIGNED_CERTIFICATES_APPLICATION_NAME,
        channel="1/stable",
        num_units=1,
    )
    deploy_vault = deploy_vault_and_wait(
        ops_test, vault_charm_path, NUM_VAULT_UNITS, status="blocked"
    )
    await asyncio.gather(
        deploy_vault,
        deploy_self_signed_certificates,
    )

    global root_token, unseal_key
    root_token, unseal_key = await initialize_unseal_authorize_vault(ops_test, APPLICATION_NAME)

    vault_app = ops_test.model.applications[APPLICATION_NAME]
    assert vault_app
    common_name = "unmatching-the-requirer.com"
    common_name_config = {
        "common_name": common_name,
    }
    await vault_app.set_config(common_name_config)
    await ops_test.model.integrate(
        relation1=f"{APPLICATION_NAME}:tls-certificates-pki",
        relation2=f"{SELF_SIGNED_CERTIFICATES_APPLICATION_NAME}:certificates",
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME, SELF_SIGNED_CERTIFICATES_APPLICATION_NAME],
        status="active",
        timeout=1000,
    )


@pytest.mark.abort_on_fail
async def test_given_vault_pki_relation_and_unmatching_common_name_when_integrate_then_cert_not_provided(
    ops_test: OpsTest, pki_requirer_charm_path: Path
):
    assert ops_test.model

    await ops_test.model.deploy(
        pki_requirer_charm_path
        if pki_requirer_charm_path
        else VAULT_PKI_REQUIRER_APPLICATION_NAME,
        application_name=VAULT_PKI_REQUIRER_APPLICATION_NAME,
        revision=VAULT_PKI_REQUIRER_REVISION,
        channel="stable",
        config={"common_name": "test.example.com", "sans_dns": "test.example.com"},
    )
    await ops_test.model.wait_for_idle(
        apps=[VAULT_PKI_REQUIRER_APPLICATION_NAME],
        status="active",
        timeout=1000,
    )

    await ops_test.model.integrate(
        relation1=f"{APPLICATION_NAME}:vault-pki",
        relation2=f"{VAULT_PKI_REQUIRER_APPLICATION_NAME}:certificates",
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME],
        status="active",
        timeout=1000,
        wait_for_exact_units=NUM_VAULT_UNITS,
    )
    await ops_test.model.wait_for_idle(
        apps=[VAULT_PKI_REQUIRER_APPLICATION_NAME],
        status="active",
        timeout=1000,
        wait_for_exact_units=1,
    )
    leader_unit = await get_leader_unit(ops_test.model, APPLICATION_NAME)
    leader_unit_address = await get_unit_address(ops_test, leader_unit.name)
    current_issuers_common_name = get_vault_pki_intermediate_ca_common_name(
        root_token=root_token,
        endpoint=leader_unit_address,
        mount="charm-pki",
    )
    assert current_issuers_common_name == "unmatching-the-requirer.com"
    action_output = await run_get_certificate_action(ops_test)
    assert action_output.get("certificate") is None


@pytest.mark.abort_on_fail
async def test_given_vault_pki_relation_and_matching_common_name_configured_when_integrate_then_cert_is_provided(
    ops_test: OpsTest,
):
    assert ops_test.model

    vault_app = ops_test.model.applications[APPLICATION_NAME]
    assert vault_app
    common_name = "example.com"
    common_name_config = {
        "common_name": common_name,
    }
    await vault_app.set_config(common_name_config)
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME],
        status="active",
        timeout=1000,
        wait_for_exact_units=NUM_VAULT_UNITS,
    )
    await ops_test.model.wait_for_idle(
        apps=[VAULT_PKI_REQUIRER_APPLICATION_NAME],
        status="active",
        timeout=1000,
        wait_for_exact_units=1,
    )
    await wait_for_status_message(
        ops_test,
        expected_message="Unit certificate is available",
        app_name=VAULT_PKI_REQUIRER_APPLICATION_NAME,
        count=1,
    )

    leader_unit = await get_leader_unit(ops_test.model, APPLICATION_NAME)
    leader_unit_address = await get_unit_address(ops_test, leader_unit.name)
    current_issuers_common_name = get_vault_pki_intermediate_ca_common_name(
        root_token=root_token,
        endpoint=leader_unit_address,
        mount="charm-pki",
    )
    action_output = await run_get_certificate_action(ops_test)
    assert current_issuers_common_name == common_name
    assert action_output["certificate"] is not None
    assert action_output["ca-certificate"] is not None
    assert action_output["csr"] is not None


async def run_get_certificate_action(ops_test: OpsTest) -> dict:
    """Run `get-certificate` on the `tls-requirer-requirer/0` unit.

    Args:
        ops_test (OpsTest): OpsTest

    Returns:
        dict: Action output
    """
    assert ops_test.model
    tls_requirer_unit = ops_test.model.units[f"{VAULT_PKI_REQUIRER_APPLICATION_NAME}/0"]
    assert isinstance(tls_requirer_unit, Unit)
    action = await tls_requirer_unit.run_action(
        action_name="get-certificate",
    )
    assert isinstance(action, Action)
    action_output = await ops_test.model.get_action_output(action_uuid=action.entity_id, wait=240)
    return action_output
