import docker
import logging
from slugify import slugify
from pathlib import Path
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

import docker


def get_last_path_part(url):
    """
    Parses a URL and returns the last part of the path.
    
    Args:
        url (str): The URL to parse.
    
    Returns:
        str: The last part of the path in the URL.
    """
    # Parse the URL
    parsed_url = urlparse(url)
    # Extract the path and get the last part using pathlib
    last_part = Path(parsed_url.path).name
    return last_part

def ensure_network_exists(client, network_name, is_external=False, network_type="bridge"):
    """
    Ensure a Docker network exists.
    
    Args:
        network_name (str): Name of the network to ensure.
        is_external (bool): If True, assumes the network is managed externally and only verifies its existence.
        network_type (str): The type of the network ('bridge' or 'overlay'). Defaults to 'bridge'.
    
    Returns:
        docker.models.networks.Network: The existing or newly created network object.
    """
    try:
        # Initialize Docker client
     

        # Check if the network already exists
        existing_networks = client.networks.list(names=[network_name])
        if existing_networks:
            logger.debug(f"Network '{network_name}' already exists.")
            return existing_networks[0]

        if is_external:
            raise ValueError(f"External network '{network_name}' does not exist.")

        # Create the network if it doesn't exist
        network = client.networks.create(
            name=network_name,
            driver=network_type,
            check_duplicate=True
        )
        logger.debug(f"Network '{network_name}' created as a '{network_type}' network.")
        return network

    except docker.errors.APIError as e:
        logger.error(f"Error interacting with Docker API: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

    return None


def create_container(
    client: docker.DockerClient,
    image: str,
    local_dir: str = None,
    name: str = None,
    env_vars: dict = {},
    ports: dict = None,
    networks: list = None,
    labels: dict = None,
    remove=False
):
    """
    Create and start a Docker container for a development environment.

    :param image: The Docker image to use.
    :param local_dir: The local directory to bind to /workspace.
    :param env_vars: A dictionary of environment variables to set in the container.
    :param container_name: The name of the container.
    :param ports: A dictionary mapping container ports to host ports, e.g., {"8080": 8080}.
    :param networks: A list of networks to connect the container to.
    """
   
    
    # Prepare port bindings
    if isinstance(ports, dict):
        port_bindings = {f"{port}/tcp": host_port for port, host_port in (ports or {}).items()}
    elif isinstance(ports, list):
        port_bindings = {f"{port}/tcp": None for port in (ports or [])}
    else:
        port_bindings = None

    logger.info(f"Creating container from image '{image}'...")
    logger.debug(f"Port bindings: {port_bindings}")
    logger.debug(f"Volumes: {local_dir}")
    

    # Create and start the container
    container = client.containers.run(
        image=image,
        name=slugify(name),
        ports=port_bindings,
        environment=env_vars,
        volumes={local_dir: {"bind": "/workspace", "mode": "rw"}} if local_dir else None,
        detach=True,
        remove=remove, 
        labels=labels,
        network_mode="bridge",  # Can be overridden by connecting to networks after creation
    )
    
    # Connect to additional networks if specified
    for net in (networks or []):
        network = client.networks.get(net)
        logger.info(f"Connecting container to network '{net}' : {network.id}")
        network.connect(container)
    
    logger.info(f"Container '{container.name}' ({container.id}) created successfully.")
    return container



def only_one_container(containers, reset = False):
    if containers:
        logger.debug(f"Found {len(containers)} containers")
        first, rest = containers[0], containers[1:]
        
        for container in rest:
            logger.debug(f"Removing container {container.name}")
            container.remove(force=True)
        
        #if first is stopped, start it. 
        if reset:
            logger.debug(f"Destroying container {first.name} because reset is True")
            first.remove(force=True)
    
        elif first.status == "exited":
            logger.debug(f"Starting container {first.name}")
            first.start()
            return first
        else:
            logger.debug(f"Container {first.name} is already running")
            return first
        
    return None

def create_novnc_container(client, config,  username, reset = False):
    # Create the container
    
    containers = client.containers.list(filters={"label": f"jtl.novnc.username={username}"}, all=True)
    
    if container := only_one_container(containers, reset):
        return container
     
    name = slugify(username)
    container_name = f"{name}-novnc"
    hostname = f"{container_name}.do.jointheleague.org"
     
    labels = {
        "jtl":  'true', 
        "jtl.novnc": 'true', 
        "jtl.novnc.username": username,
        
        "caddy": hostname,
        "caddy.@ws.0_header": "Connection *Upgrade*",
        "caddy.@ws.1_header": "Upgrade websocket",
        "caddy.0_reverse_proxy": "@ws {{upstreams 6080}}",
        "caddy.1_reverse_proxy": "{{upstreams 6080}}"
    }
    
    container = create_container(
        client,
        image=config.IMAGES_NOVNC,
        name=container_name,
        #ports=["6080"],
        labels=labels,
        networks=["x11", "jtlctl", "caddy"],
    )

    # Start the container
    container.start()

    return container

def create_cs_container(client, config, image, username, env_vars, vnc_id=None, reset=False):
    # Create the container
    
    containers = client.containers.list(filters={"label": f"jtl.codeserver.username={username}"}, all=True)
    
    if container := only_one_container(containers, reset):
        return container
       
    vnc_c = client.containers.get(vnc_id)   
       
    name = slugify(username)
    container_name = f"{slugify(username)}"
       
    vnc_host = vnc_c.labels.get("caddy")
    vnc_url = f"https://{vnc_host}"

    password = "code4life"

    _env_vars = {
        "PASSWORD": password,
        "DISPLAY": f"{vnc_c.name}:0",
        "VNC_URL": vnc_url,
        "KST_REPORTING_URL": config.KST_REPORTING_URL,
        "KST_CONTAINER_ID": name,
		"KST_REPORT_RATE": config.KST_REPORT_RATE if hasattr(config, "KST_REPORT_RATE") else 30
    }
    
    env_vars = {**_env_vars, **env_vars}
    
    labels = {
        "jtl": 'true', 
        "jtl.codeserver": 'true',  
        "jtl.codeserver.username": username,
        "jt.codeserver.password": password,
        "jtl.codeserver.vnc": vnc_c.name,
        "jtl.codeserver.start_time": datetime.now(pytz.timezone('America/Los_Angeles')).isoformat(),
        
        "caddy": config.HOSTNAME_TEMPLATE.format(username=slugify(username)),
        "caddy.reverse_proxy": "{{upstreams 8080}}"
    }
    
    container = create_container(
        client,
        image=image,
        env_vars=env_vars,
        name=container_name,
        local_dir=None,
        labels=labels,
        networks=["x11", "jtlctl", "caddy"],
    )

    # Start the container
    container.start()

    return container

def create_cs_pair(client, config, image, username, env_vars = {}, reset = False):
    """ Create a pair of containers: a novnc container and a codeserver container.
    
    """
    nvc = create_novnc_container(client, config, username=username, reset=reset)
    pa = create_cs_container(client, config, image, username=username, env_vars = env_vars, vnc_id=nvc.id, reset=reset)
    return nvc, pa


def container_status(client, username):
    containers = client.containers.list(filters={"label": f"jtl.codeserver.username={username}"}, all=True)
    if containers:
        return containers[0].status
    return None