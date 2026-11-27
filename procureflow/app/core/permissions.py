"""
Authorisation helpers — IBC ProcureFlow.

All functions accept the ``user`` dict returned by ``get_current_user`` and
raise ``HTTP 403 Forbidden`` when the caller lacks the required permission.

Usage::

    @router.post("/some-endpoint")
    def my_endpoint(user: dict = Depends(get_current_user)):
        require_manager(user)
        ...
"""

from fastapi import HTTPException, status


def require_manager(user: dict) -> None:
    """
    Assert that *user* has the ``manager`` role.

    Raises ``HTTP 403`` for employees or unauthenticated callers.
    """
    if user.get("role") != "manager":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Manager access required.",
        )


def require_procure_access(user: dict) -> None:
    """
    Assert that *user* may raise or manage purchase enquiries / orders.

    Managers always pass.  Employees must have ``can_procure = True``.
    Raises ``HTTP 403`` otherwise.
    """
    if user.get("role") == "manager" or user.get("can_procure"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: Procurement access required.",
    )


def require_receive_access(user: dict) -> None:
    """
    Assert that *user* may post Goods Receipt Notes (GRNs).

    Managers always pass.  Employees must have ``can_receive = True``.
    Raises ``HTTP 403`` otherwise.
    """
    if user.get("role") == "manager" or user.get("can_receive"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: Receiving (GRN) access required.",
    )


def require_employee_procure_access(user: dict) -> None:
    if user.get("role") != "employee" or not user.get("can_procure"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: Employee procurement access required.")


def require_employee_receive_access(user: dict) -> None:
    if user.get("role") != "employee" or not user.get("can_receive"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: Employee receiving access required.")


def check_pr_approval_segregation(user: dict, pr: dict) -> None:
    """
    Enforce segregation of duties for Purchase Requisition approvals.

    A manager may not approve a PR that *they* created (``requested_by``).
    Raises ``HTTP 403`` when the check fails.
    """
    if pr.get("requested_by") == user.get("id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Segregation of Duties: You cannot approve your own Purchase Requisition.",
        )


def check_po_approval_segregation(user: dict, po: dict) -> None:
    """
    Enforce segregation of duties for Purchase Order approvals.

    A manager may not approve a PO that *they* created (``created_by``).
    Raises ``HTTP 403`` when the check fails.
    """
    if po.get("created_by") == user.get("id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Segregation of Duties: You cannot approve a Purchase Order you created.",
        )
