# Experimental external API using FastAPI
from datetime import datetime, timedelta
from typing import List, Optional, Union

import base64
import bcrypt
from fastapi import Depends, FastAPI, HTTPException, status, Body
from fastapi.responses import PlainTextResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from functools import partial
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from shakenfist import logutil
from shakenfist.daemons import daemon
from shakenfist import db as sf_db
from shakenfist.config import config
from shakenfist.util import get_version
from shakenfist import virt


_jwt_secret = config.AUTH_SECRET_SEED.get_secret_value()
ACCESS_TOKEN_EXPIRES_MINUTES = 30


class Token(BaseModel):
    access_token: str
    token_type: str


tags_metadata = [
    {
        "name": "authentication",
        "description": "Authentication and operations on namespaces.",
    },
    {
        "name": "instances",
        "description": "Operations on instances.",
    },
    {
        "name": "networks",
        "description": "Operations on networks",
    },
]

app = FastAPI(
    title="Shaken Fist API",
    description="The REST API for Shaken Fist",
    version=get_version(),
    openapi_tags=tags_metadata,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


class Identity(BaseModel):
    namespace: str = Field(..., example='system')


async def get_identity(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, _jwt_secret, algorithms=['HS256'])
        namespace: str = payload.get("sub")
        if namespace is None:
            raise credentials_exception
        return Identity(namespace=namespace)
    except JWTError:
        raise credentials_exception


async def get_admin(identity: Identity = Depends(get_identity)):
    if identity.namespace == 'system':
        return identity
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not an admin")


async def get_db():
    return sf_db


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


TESTING = False
SCHEDULER = None


@app.get('/', response_class=PlainTextResponse)
async def root():
    "Return the banner"
    return 'Shaken Fist REST API service'


@app.get('/admin/locks')
async def admin_locks(identity: Identity = Depends(get_admin),
                      db=Depends(get_db)):
    "Return all locks in etcd"
    return db.get_existing_locks()


@app.post('/token', tags=['authentication'], response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    namespace = form_data.username
    key = form_data.password
    token = _get_token(namespace, key)
    return {'access_token': token, 'token_type': 'bearer'}


def _get_token(db, namespace: str, key: str) -> str:
    service_key, keys = _get_keys(db, namespace)
    if service_key and key == service_key:
        return _create_access_token(
            data={"sub": namespace},
            expires_delta=ACCESS_TOKEN_EXPIRES_MINUTES,
            )
    for possible_key in keys:
        if bcrypt.checkpw(key.encode('utf-8'), possible_key):
            return _create_access_token(
                data={"sub": namespace},
                expires_delta=ACCESS_TOKEN_EXPIRES_MINUTES,
            )
    raise HTTPException(
        status_code=400,
        detail='Incorrect namespace or key',
    )


def _create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, _jwt_secret, algorithm='HS256')
    return encoded_jwt


def _get_keys(db, namespace):
    rec = db.get_namespace(namespace)
    if not rec:
        return (None, [])

    keys = []
    for key_name in rec.get('keys', {}):
        keys.append(base64.b64decode(rec['keys'][key_name]))
    return (rec.get('service_key'), keys)


class AuthRequest(BaseModel):
    namespace: str
    key: str


@app.post('/auth', tags=['authentication'])
async def auth(req: AuthRequest, db=Depends(get_db)):
    'Request an auth token'
    token = _get_token(db, req.namespace, req.key)
    return {'access_token': token}


@app.get('/auth/namespaces', tags=['authentication'], response_model=List[str])
async def namespaces(identity: Identity = Depends(get_admin),
                     db=Depends(get_db)):
    'Return the names of all namespaces'
    return [rec['name'] for rec in db.list_namespaces()]


illegal_key_name = HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                 detail='illegal key name')


def assert_valid_key_name(key_name):
    if key_name == 'service_key':
        raise illegal_key_name


class NewEmptyNS(BaseModel):
    namespace: str


class UpdateNSKey(BaseModel):
    key_name: str
    key: str


class NewSingletonNS(NewEmptyNS, UpdateNSKey):
    pass


NewNSRequest = Union[NewSingletonNS, NewEmptyNS]


@app.post('/auth/namespaces', tags=['authentication'], response_model=str)
async def new_namespace(ns: NewNSRequest,
                        identity: Identity = Depends(get_admin),
                        db=Depends(get_db)):
    'Create a new namespace'
    namespace = ns.namespace
    with db.get_lock('namespace', None, 'all', op='Namespace update'):
        rec = db.get_namespace(namespace)
        if not rec:
            rec = {
                'name': namespace,
                'keys': {}
            }

        # Allow shortcut of creating key at same time as the namespace
        if ns.key_name:
            key_name = ns.key_name
            key = ns.key
            assert_valid_key_name(key_name)

            encoded = str(base64.b64encode(bcrypt.hashpw(
                key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
            rec['keys'][key_name] = encoded

        # Initialise metadata
        db.persist_metadata('namespace', namespace, {})
        db.persist_namespace(namespace, rec)

    return namespace


@app.delete('/auth/namespaces/{namespace}', tags=['authentication'])
async def delete_namespace(namespace: str,
                           identity: Identity = Depends(get_admin),
                           db=Depends(get_db)):
    if namespace == 'system':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail='you cannot delete the system namespace')

    # The namespace must be empty
    instances = []
    deleted_instances = []
    for i in virt.Instances([partial(virt.namespace_filter, namespace)]):
        state = db.get_instance_attribute(i.uuid, 'state')
        if state['state'] in ['deleted', 'error']:
            deleted_instances.append(i.uuid)
        else:
            instances.append(i.uuid)
    if len(instances) > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='you cannot delete a namespace with instances')

    networks = []
    for n in db.get_networks(all=True, namespace=namespace):
        # Networks in 'deleting' state are regarded as "live" networks.
        # They in a transient state. If they hang in that state we want to
        # know. They will block deletion of a namespace thus giving notice
        # of the problem.
        if n['state'] not in ['deleted', 'error']:
            networks.append(n['uuid'])
    if len(networks) > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='you cannot delete a namespace with networks')

    db.delete_namespace(namespace)
    db.delete_metadata('namespace', namespace)


@app.get('/auth/namespaces/{namespace}/keys', tags=['authentication'],
         response_model=List[str])
async def namespace_keys(namespace: str,
                         identity: Identity = Depends(get_admin),
                         db=Depends(get_db)):
    'List all key names in a namespace'
    rec = db.get_namespace(namespace)
    if not rec:
        raise no_such_namespace
    return list(rec)


no_such_namespace = HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                  detail='namespace does not exist')


@app.post('/auth/namespaces/{namespace}/keys', tags=['authentication'])
async def new_namespace_key(namespace: str,
                            u: UpdateNSKey,
                            identity: Identity = Depends(get_admin)):
    _namespace_keys_putpost(namespace, u)
    return u.key_name


def _namespace_keys_putpost(db, namespace: str, u: UpdateNSKey):
    key_name = u.key_name
    key = u.key
    assert_valid_key_name(key_name)
    with db.get_lock('namespace', None, 'all', op='Namespace key update'):
        rec = db.get_namespace(namespace)
        if not rec:
            raise no_such_namespace
        encoded = str(base64.b64encode(bcrypt.hashpw(
            key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
        rec['keys'][key_name] = encoded

        db.persist_namespace(namespace, rec)


no_such_key = HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='key name not found in namespace')


@app.delete('/auth/namespaces/{namespace}/keys/{key_name}',
            tags=['authentication'])
async def delete_namespace_key(namespace: str,
                               key_name: str,
                               identity: Identity = Depends(get_admin),
                               db=Depends(get_db)):
    with db.get_lock('namespace', None, namespace, op='Namespace key delete'):
        ns = db.get_namespace(namespace)
        if ns.get('keys') and key_name in ns['keys']:
            del ns['keys'][key_name]
        else:
            raise no_such_key
        db.persist_namespace(namespace, ns)


@app.put('/auth/namespaces/{namespace}/keys/{key_name}',
         tags=['authentication'])
async def update_namespace_key(namespace: str, key_name: str,
                               key: str = Body(...),
                               identity: Identity = Depends(get_admin),
                               db=Depends(get_db)):
    rec = db.get_namespace(namespace)
    if not rec:
        raise no_such_namespace
    if key_name not in rec['keys']:
        raise no_such_key
    _namespace_keys_putpost(db, namespace,
                            UpdateNSKey(key_name=key_name, key=key))
    return key_name


@app.get('/auth/namespace/{namespace}/metadata', tags=['authentication'])
async def auth_metadata():
    pass


@app.post('/auth/namespace/{namespace}/metadata', tags=['authentication'])
async def new_auth_metadata():
    pass


@app.put('/auth/namespace/{namespace}/metadata/{key}',
         tags=['authentication'])
async def update_auth_metadatum():
    pass


@app.delete('/auth/namespace/{namespace}/metadata/{key}',
            tags=['authentication'])
async def delete_auth_metadatum():
    pass


@app.get('/instances', tags=['instances'])
async def instances():
    pass


@app.get('/instances/{instance}', tags=['instances'])
async def instance(instance: str):
    pass


@app.get('/instances/{instance}/events', tags=['instances'])
async def instance_events(instance: str):
    pass


@app.get('/instances/{instance}/interfaces', tags=['instances'])
async def instance_interfaces(instance: str):
    pass


@app.post('/instances/{instance}/snapshot', tags=['instances'])
async def instance_snapshot(instance: str):
    pass


@app.post('/instances/{instance}/rebootsoft', tags=['instances'])
async def instance_rebootsoft(instance: str):
    pass


@app.post('/instances/{instance}/reboothard', tags=['instances'])
async def instance_reboothard(instance: str):
    pass


@app.post('/instances/{instance}/poweroff', tags=['instances'])
async def instance_poweroff(instance: str):
    pass


@app.post('/instances/{instance}/poweron', tags=['instances'])
async def instance_poweron(instance: str):
    pass


@app.post('/instances/{instance}/pause', tags=['instances'])
async def instance_pause(instance: str):
    pass


@app.post('/instances/{instance}/unpause', tags=['instances'])
async def instance_unpause(instance: str):
    pass


@app.get('/interfaces/{interface}')
async def interface(interface: str):
    pass


@app.get('/interfaces/{interface}/float')
async def interface_float(interface: str):
    pass


@app.get('/interfaces/{interface}/defloat')
async def interface_defloat(interface: str):
    pass


@app.get('/instances/{instance}/metadata', tags=['instances'])
async def instance_metadata(instance: str):
    pass


@app.get('/instances/{instance}/metadata/{key}', tags=['instances'])
async def instance_metadatum(instance: str, key: str):
    pass


@app.get('/instances/{instance}/consoledata', tags=['instances'])
async def instance_consoledata(instance: str):
    pass


@app.get('/images')
async def images():
    pass


@app.get('/images/events')
async def image_events():
    pass


@app.get('/networks', tags=['networks'])
async def networks():
    pass


@app.get('/networks/{network}', tags=['networks'])
async def network(network: str):
    pass


@app.get('/networks/{network}/events', tags=['networks'])
async def network_events(network: str):
    pass


@app.get('/networks/{network}/interfaces', tags=['networks'])
async def network_interfaces(network: str):
    pass


@app.get('/networks/{network}/metadata', tags=['networks'])
async def network_metadata(network: str):
    pass


@app.get('/networks/{network}/metadata/{key}', tags=['networks'])
async def network_metadatum(network: str, key: str):
    pass


@app.post('/networks/{network}/ping/{address}', tags=['networks'])
async def network_ping(network: str, address: str):
    pass


@app.get('/nodes')
async def nodes():
    pass
