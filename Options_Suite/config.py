import logging
from typing import Dict, Union

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PricingConfig:
    """
    Configuration class for option pricing parameters.
    """

    def __init__(self):
        # Default simulation parameters for the plain LSM pricer (MC.py's
        # AmericanLSMPricer). These match the values that have actually been in use
        # (AmericanLSMPricer's own class defaults) -- previously this config class was
        # never wired in, so it silently held stale, unused defaults (10000/50) that
        # conflicted with the real ones (50000/100).
        self.simulations = 50000
        self.steps = 100
        self.option_type = 'call'  # 'call' or 'put'

        # Heston LSM is far more expensive per path (extra variance-process
        # simulation, plus ~20 reprices for Greeks), so it intentionally uses smaller
        # sim/step counts. Two separate presets: a single Heston run (menu option 5)
        # vs. the lighter one used inside "Run all models & compare" (menu option 6),
        # where four other models are also being priced in the same pass.
        self.heston_sims = 12000
        self.heston_steps = 200
        self.heston_compare_sims = 8000
        self.heston_compare_steps = 150

        # Market data preferences
        self.treasury_proxy = 'long_term'  # 'short_term' or 'long_term'

        # Validation thresholds
        self.min_volatility = 0.05
        self.max_volatility = 1.0
        self.min_risk_free_rate = 0.0
        self.max_risk_free_rate = 0.25

    def validate_config(self) -> bool:
        """
        Validate that all configuration values are within reasonable ranges.
        """
        valid = True

        if self.simulations <= 0:
            logger.warning("Simulations must be positive")
            valid = False

        if self.steps <= 0:
            logger.warning("Steps must be positive")
            valid = False

        if self.option_type not in ['call', 'put']:
            logger.warning("Option type must be 'call' or 'put'")
            valid = False

        if self.treasury_proxy not in ['short_term', 'long_term']:
            logger.warning("Treasury proxy must be 'short_term' or 'long_term'")
            valid = False

        return valid

    def to_dict(self) -> Dict[str, Union[int, str]]:
        """
        Convert configuration to dictionary.
        """
        return {
            'simulations': self.simulations,
            'steps': self.steps,
            'heston_sims': self.heston_sims,
            'heston_steps': self.heston_steps,
            'heston_compare_sims': self.heston_compare_sims,
            'heston_compare_steps': self.heston_compare_steps,
            'option_type': self.option_type,
            'treasury_proxy': self.treasury_proxy
        }