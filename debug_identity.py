from tests.test_dealer_exposure_executor import FakeAdapter
print('Module:', type(FakeAdapter).__module__)
print('Qualname:', type(FakeAdapter).__qualname__)
print('Identity:', f"{type(FakeAdapter).__module__}:{type(FakeAdapter).__qualname__}.__call__")