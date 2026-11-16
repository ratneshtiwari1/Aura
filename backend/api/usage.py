from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from backend.services.token_usage_service import usage_summary, usage_csv

router = APIRouter(prefix='/api/usage', tags=['token usage'])


@router.get('/tokens')
def tokens(scope: Literal['all', 'backend', 'chat'] = 'all'):
    try:
        return usage_summary(scope)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get('/tokens/export')
def export_tokens(scope: Literal['all', 'backend', 'chat'] = 'all'):
    try:
        data = usage_csv(scope)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return Response(data, media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="aura-token-usage-{scope}.csv"'})
