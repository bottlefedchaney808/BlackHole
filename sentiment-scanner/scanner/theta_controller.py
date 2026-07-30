# C:\Users\bottl\FinancialDevelopment\sentiment-scanner\scanner\theta_controller.py
#
# REDIRECT STUB -- this file previously contained its own standalone
# ThetaDataController implementation with hardcoded Cloudflare Access
# credentials in plaintext. Consolidated into shared/thetadata.py along with
# the other two duplicate clients (Options_Suite/thetadata_controller.py,
# Vol_Suite/thetadata_client.py); credentials now come from the project-root
# .env / environment (THETADATA_CF_ACCESS_CLIENT_ID / _SECRET) via
# shared.config -- same values as before, nothing to reconfigure.
#
# All public names are re-exported from the single shared source; imports
# from `scanner.theta_controller` continue to work without modification.

from shared.thetadata import (
    ThetaDataController,
    strike_to_theta,
    strike_from_theta,
)

__all__ = [
    "ThetaDataController",
    "strike_to_theta",
    "strike_from_theta",
]
