import asyncio
import logging
from pathlib import Path

import pytest
from juju.application import Application
from pytest_operator.plugin import OpsTest

from tests.integration.config import (
    APPLICATION_NAME,
    JUJU_FAST_INTERVAL,
    NUM_VAULT_UNITS,
    VAULT_KV_REQUIRER_1_APPLICATION_NAME,
    VAULT_KV_REQUIRER_2_APPLICATION_NAME,
)
from tests.integration.helpers import (
    authorize_charm_and_wait,
    crash_pod,
    deploy_vault_and_wait,
    get_leader_unit,
    get_vault_client,
    initialize_vault_leader,
    unseal_all_vault_units_and_wait,
    unseal_vault_unit,
)

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_given_vault_kv_requirer_deployed_when_vault_kv_relation_created_then_status_is_active(
    ops_test: OpsTest, vault_charm_path: Path, kv_requirer_charm_path: Path
):
    assert ops_test.model

    deploy_vault = asyncio.create_task(
        deploy_vault_and_wait(
            ops_test,
            vault_charm_path,
            num_units=NUM_VAULT_UNITS,
        )
    )

    await ops_test.model.deploy(
        kv_requirer_charm_path,
        application_name=VAULT_KV_REQUIRER_1_APPLICATION_NAME,
    )
    async with ops_test.fast_forward(fast_interval=JUJU_FAST_INTERVAL):
        await ops_test.model.wait_for_idle(
            apps=[VAULT_KV_REQUIRER_1_APPLICATION_NAME],
        )
    await deploy_vault
    root_token, unseal_key = await initialize_vault_leader(ops_test, APPLICATION_NAME)
    leader = await get_leader_unit(ops_test.model, APPLICATION_NAME)
    vault = await get_vault_client(ops_test, leader, root_token)

    await unseal_vault_unit(vault, unseal_key)
    await authorize_charm_and_wait(ops_test, root_token)
    await unseal_all_vault_units_and_wait(ops_test, unseal_key, root_token)

    assert ops_test.model

    await ops_test.model.integrate(
        relation1=f"{APPLICATION_NAME}:vault-kv",
        relation2=f"{VAULT_KV_REQUIRER_1_APPLICATION_NAME}:vault-kv",
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME, VAULT_KV_REQUIRER_1_APPLICATION_NAME],
        status="active",
        timeout=1000,
    )


@pytest.mark.abort_on_fail
async def test_given_vault_kv_requirer_related_when_create_secret_then_secret_is_created(
    ops_test: OpsTest,
):
    assert ops_test.model
    secret_key = "test-key"
    secret_value = "test-value"
    vault_kv_application = ops_test.model.applications[VAULT_KV_REQUIRER_1_APPLICATION_NAME]
    assert isinstance(vault_kv_application, Application)
    vault_kv_unit = vault_kv_application.units[0]
    vault_kv_create_secret_action = await vault_kv_unit.run_action(
        action_name="create-secret",
        key=secret_key,
        value=secret_value,
    )

    await ops_test.model.get_action_output(
        action_uuid=vault_kv_create_secret_action.entity_id, wait=30
    )

    vault_kv_get_secret_action = await vault_kv_unit.run_action(
        action_name="get-secret",
        key=secret_key,
    )

    action_output = await ops_test.model.get_action_output(
        action_uuid=vault_kv_get_secret_action.entity_id, wait=30
    )

    assert action_output["value"] == secret_value


@pytest.mark.abort_on_fail
async def test_given_vault_kv_requirer_related_and_requirer_pod_crashes_when_create_secret_then_secret_is_created(
    ops_test: OpsTest,
):
    secret_key = "test-key"
    secret_value = "test-value"
    assert ops_test.model
    vault_kv_application = ops_test.model.applications[VAULT_KV_REQUIRER_1_APPLICATION_NAME]
    assert isinstance(vault_kv_application, Application)
    vault_kv_unit = vault_kv_application.units[0]
    k8s_namespace = ops_test.model.name

    crash_pod(
        name=f"{VAULT_KV_REQUIRER_1_APPLICATION_NAME}-0",
        namespace=k8s_namespace,
    )

    await ops_test.model.wait_for_idle(
        apps=[VAULT_KV_REQUIRER_1_APPLICATION_NAME],
        status="active",
        timeout=1000,
        wait_for_exact_units=1,
    )

    vault_kv_create_secret_action = await vault_kv_unit.run_action(
        action_name="create-secret",
        key=secret_key,
        value=secret_value,
    )

    await ops_test.model.get_action_output(
        action_uuid=vault_kv_create_secret_action.entity_id, wait=30
    )

    vault_kv_get_secret_action = await vault_kv_unit.run_action(
        action_name="get-secret",
        key=secret_key,
    )

    action_output = await ops_test.model.get_action_output(
        action_uuid=vault_kv_get_secret_action.entity_id, wait=30
    )

    assert action_output["value"] == secret_value


@pytest.mark.abort_on_fail
async def test_given_multiple_kv_requirers_related_when_secrets_created_then_secrets_created(
    ops_test: OpsTest, kv_requirer_charm_path: Path
):
    assert ops_test.model
    await ops_test.model.deploy(
        kv_requirer_charm_path,
        application_name=VAULT_KV_REQUIRER_2_APPLICATION_NAME,
    )
    async with ops_test.fast_forward(fast_interval=JUJU_FAST_INTERVAL):
        await ops_test.model.wait_for_idle(
            apps=[VAULT_KV_REQUIRER_2_APPLICATION_NAME],
        )
    await ops_test.model.integrate(
        relation1=f"{APPLICATION_NAME}:vault-kv",
        relation2=f"{VAULT_KV_REQUIRER_2_APPLICATION_NAME}:vault-kv",
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME, VAULT_KV_REQUIRER_2_APPLICATION_NAME],
        status="active",
        timeout=1000,
    )
    secret_key = "test-key-2"
    secret_value = "test-value-2"
    assert ops_test.model
    vault_kv_application = ops_test.model.applications[VAULT_KV_REQUIRER_2_APPLICATION_NAME]
    assert isinstance(vault_kv_application, Application)
    vault_kv_unit = vault_kv_application.units[0]
    vault_kv_create_secret_action = await vault_kv_unit.run_action(
        action_name="create-secret",
        key=secret_key,
        value=secret_value,
    )

    await ops_test.model.get_action_output(
        action_uuid=vault_kv_create_secret_action.entity_id, wait=30
    )

    vault_kv_get_secret_action = await vault_kv_unit.run_action(
        action_name="get-secret",
        key=secret_key,
    )

    action_output = await ops_test.model.get_action_output(
        action_uuid=vault_kv_get_secret_action.entity_id, wait=30
    )

    assert action_output["value"] == secret_value
