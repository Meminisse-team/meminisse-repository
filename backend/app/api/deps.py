from typing import Annotated

from fastapi import Depends

from app.gateways.factory import Gateways, get_gateways

GatewaysDep = Annotated[Gateways, Depends(get_gateways)]
