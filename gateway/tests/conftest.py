import pytest

from artie_gateway.service import Gateway
from artie_gateway.store import InMemoryStore

from gw_fakes import Clock, FakeNetwork, make_config


@pytest.fixture
def network() -> FakeNetwork:
    return FakeNetwork()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock) -> InMemoryStore:
    return InMemoryStore(clock)


@pytest.fixture
def gateway(network, store, clock) -> Gateway:
    return Gateway(make_config(), store, network, clock, sleep=clock.advance)
