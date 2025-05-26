from pathlib import Path

import pytest
from pytest_operator.plugin import OpsTest

from tests.integration.config import (
    APPLICATION_NAME,
    LOKI_APPLICATION_NAME,
    NUM_VAULT_UNITS,
    PROMETHEUS_APPLICATION_NAME,
)
from tests.integration.helpers import deploy_vault_and_wait, initialize_unseal_authorize_vault


@pytest.mark.skip_if_deployed
@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest, vault_charm_path: Path):
    """Build and deploy the application."""
    assert ops_test.model
    await deploy_vault_and_wait(
        ops_test,
        charm_path=vault_charm_path,
        num_units=NUM_VAULT_UNITS,
        status="blocked",
    )
    await ops_test.model.deploy(
        "prometheus-k8s",
        application_name=PROMETHEUS_APPLICATION_NAME,
        trust=True,
    )
    await ops_test.model.deploy(
        "loki-k8s",
        application_name=LOKI_APPLICATION_NAME,
        trust=True,
        channel="stable",
    )
    await ops_test.model.wait_for_idle(
        apps=[PROMETHEUS_APPLICATION_NAME, LOKI_APPLICATION_NAME],
        raise_on_error=False,  # Prometheus-k8s can fail on deploy
        timeout=120,
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME],
        status="blocked",
        timeout=600,
        wait_for_exact_units=NUM_VAULT_UNITS,
    )
    await initialize_unseal_authorize_vault(ops_test, APPLICATION_NAME)


@pytest.mark.abort_on_fail
async def test_given_prometheus_deployed_when_relate_vault_to_prometheus_then_status_is_active(
    ops_test: OpsTest,
):
    assert ops_test.model
    await ops_test.model.integrate(
        relation1=f"{APPLICATION_NAME}:metrics-endpoint",
        relation2=f"{PROMETHEUS_APPLICATION_NAME}:metrics-endpoint",
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME, APPLICATION_NAME],
        status="active",
        timeout=1000,
    )


@pytest.mark.abort_on_fail
async def test_given_loki_deployed_when_relate_vault_to_loki_then_status_is_active(
    ops_test: OpsTest,
):
    assert ops_test.model
    await ops_test.model.integrate(
        relation1=f"{APPLICATION_NAME}:logging",
        relation2=f"{LOKI_APPLICATION_NAME}",
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME, LOKI_APPLICATION_NAME],
        status="active",
        timeout=1000,
    )
