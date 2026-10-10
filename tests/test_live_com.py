"""
Tests for SAP2000 Live COM session.
"""

from unittest.mock import MagicMock
import pytest
from struct_link.backends.csi.sap2000.live_com import Sap2000LiveSession


def test_sap2000_live_session_initial_state():
    session = Sap2000LiveSession()
    assert not session.is_connected()
    with pytest.raises(RuntimeError, match="No active SAP2000 session"):
        session.get_active_model()


def test_sap2000_live_session_mocked_results():
    session = Sap2000LiveSession()
    mock_model = MagicMock()
    # Mock Results.JointDispl
    # Return code 0, 1 result, point "1", u1=0.01, u2=0.02, u3=-0.05, r1=0, r2=0, r3=0
    mock_model.Results.JointDispl.return_value = (
        0, "", 1, ("1",), ("1",), ("DEAD",), ("",), (1,),
        (0.01,), (0.02,), (-0.05,), (0.0,), (0.0,), (0.0,)
    )
    # Mock Results.JointReact
    mock_model.Results.JointReact.return_value = (
        0, "", 1, ("1",), ("1",), ("DEAD",), ("",), (1,),
        (10.0,), (20.0,), (50.0,), (0.0,), (0.0,), (0.0,)
    )

    session._sap_model = mock_model
    assert session.is_connected()

    res = session.get_analysis_results(case_name="DEAD")
    assert res.analysis_case_id == "DEAD"
    assert "1" in res.node_results
    assert res.node_results["1"].displacement.ux == 0.01
    assert res.node_results["1"].reaction.fz == 50.0
