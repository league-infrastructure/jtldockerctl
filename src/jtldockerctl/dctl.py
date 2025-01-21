import docker
import logging

logger = logging.getLogger(__name__)

def create_container(
    client: docker.DockerClient,
    image: str,
    local_dir: str = None,
    name: str = None,
    env_vars: dict = {},
    ports: dict = None,
    networks: list = None,
    labels: dict = None,
    remove=True
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
    
    
    try:
        # Create and start the container
        container = client.containers.run(
            image=image,
            name=name,
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

    except docker.errors.APIError as e:
        print(f"An error occurred: {e}")
        return None

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
     
    labels = {
        "jtl":  'true', 
        "jtl.novnc": 'true', 
        "jtl.novnc.username": username,
        
        "caddy": "novnc.do.jointheleague.org",
        "caddy.@ws.0_header": "Connection *Upgrade*",
        "caddy.@ws.1_header": "Upgrade websocket",
        "caddy.0_reverse_proxy": "@ws {{upstreams 6080}}",
        "caddy.1_reverse_proxy": "{{upstreams 6080}}"
    }
    
    container = create_container(
        client,
        image=config['novnc_image'],
        name=None,
        ports=["6080"],
        labels=labels,
        networks=["x11", "caddy"],
    )

    # Start the container
    container.start()

    return container