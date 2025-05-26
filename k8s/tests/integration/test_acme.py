import asyncio
import logging
import pdb
from pathlib import Path

import pytest
import requests
from pytest_operator.plugin import OpsTest

from tests.integration.config import (
    APPLICATION_NAME,
    JUJU_FAST_INTERVAL,
    NUM_VAULT_UNITS,
    SELF_SIGNED_CERTIFICATES_APPLICATION_NAME,
    SELF_SIGNED_CERTIFICATES_REVISION,
)
from tests.integration.helpers import (
    deploy_vault_and_wait,
    get_leader_unit,
    get_unit_address,
    initialize_unseal_authorize_vault,
)

logger = logging.getLogger(__name__)


async def verify_acme_configured(ops_test: OpsTest, app_name: str) -> bool:
    assert ops_test.model
    leader_unit = await get_leader_unit(ops_test.model, app_name)
    leader_ip = await get_unit_address(ops_test, leader_unit.name)
    url = f"https://{leader_ip}:8200/v1/charm-acme/acme/directory"

    retry_count = 3
    for attempt in range(retry_count):
        try:
            response = requests.get(url, verify=False)
            if response.status_code == 200 and "newNonce" in response.json():
                return True
            if response.status_code == 403:
                logger.warning("ACME not available yet")
        except (requests.RequestException, ValueError) as e:
            logger.warning("ACME check attempt %s/%s failed: %s", attempt + 1, retry_count, str(e))

        if attempt < retry_count - 1:
            fast_interval_in_seconds = int(JUJU_FAST_INTERVAL[:-1])
            await asyncio.sleep(fast_interval_in_seconds)

    pdb.set_trace()
    return False


@pytest.mark.abort_on_fail
async def test_given_tls_certificates_acme_relation_when_integrate_then_status_is_active_and_acme_configured(
    ops_test: OpsTest, vault_charm_path: Path
):
    assert ops_test.model
    deploy_vault = deploy_vault_and_wait(
        ops_test, vault_charm_path, NUM_VAULT_UNITS, status="blocked"
    )
    deploy_self_signed_certificates = ops_test.model.deploy(
        SELF_SIGNED_CERTIFICATES_APPLICATION_NAME,
        application_name=SELF_SIGNED_CERTIFICATES_APPLICATION_NAME,
        channel="1/stable",
        revision=SELF_SIGNED_CERTIFICATES_REVISION,
    )
    await asyncio.gather(deploy_vault, deploy_self_signed_certificates)
    await initialize_unseal_authorize_vault(ops_test, APPLICATION_NAME)

    vault_app = ops_test.model.applications[APPLICATION_NAME]
    assert vault_app
    common_name = "unmatching-the-requirer.com"
    common_name_config = {
        "common_name": common_name,
    }
    await vault_app.set_config(common_name_config)
    await ops_test.model.integrate(
        relation1=f"{APPLICATION_NAME}:tls-certificates-acme",
        relation2=f"{SELF_SIGNED_CERTIFICATES_APPLICATION_NAME}:certificates",
    )
    await ops_test.model.wait_for_idle(
        apps=[APPLICATION_NAME, SELF_SIGNED_CERTIFICATES_APPLICATION_NAME],
        status="active",
        timeout=1000,
    )
    async with ops_test.fast_forward(fast_interval=JUJU_FAST_INTERVAL):
        # FIXME: This seems to rely on the reconcile loop, so we wait in fast forward
        assert await verify_acme_configured(ops_test, APPLICATION_NAME)
