"""Pure shared package: every rule of the system has exactly one implementation here.

Imports nothing heavy. Only ``qmc_core.sim`` and ``qmc_core.fingerprint`` import NumPy,
so the API zip (which has no NumPy) can import everything else.
"""
