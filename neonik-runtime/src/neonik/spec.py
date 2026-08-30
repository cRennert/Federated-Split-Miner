from abc import ABC, abstractmethod
from typing import Any

from neonik.neon import protocol
from neonik.neon.network import Network


def normalize_identifier(identifier: str) -> str:
    return identifier.lower().replace("_", "-")


class ComponentProperty(ABC):
    __identifier: str
    __description: str

    def __init__(self, identifier: str, description: str):
        self.__identifier = identifier
        self.__description = description

    def validate(self, value: Any) -> None:
        try:
            self.validate0(value)
        except:
            raise ValueError(f"Property \"{self.identifier}\" receive invalid value \"{value}\".")

    def serialize(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "description": self.description
        }

    @abstractmethod
    def validate0(self, value: Any) -> None:
        pass

    @abstractmethod
    def json_encode_value(self, value: Any) -> Any:
        pass

    @property
    def identifier(self) -> str:
        return self.__identifier

    @property
    def description(self) -> str:
        return self.__description


class IntegerComponentProperty(ComponentProperty):
    def __init__(self, identifier: str, description: str):
        super().__init__(identifier, description)

    def serialize(self) -> dict[str, Any]:
        return {
            **super().serialize(),
            "type": "integer"
        }

    def validate0(self, value: Any) -> None:
        int(value)

    def json_encode_value(self, value: Any) -> Any:
        return value


class DecimalComponentProperty(ComponentProperty):
    def __init__(self, identifier: str, description: str):
        super().__init__(identifier, description)

    def serialize(self) -> dict[str, Any]:
        return {
            **super().serialize(),
            "type": "decimal"
        }

    def validate0(self, value: Any) -> None:
        float(value)

    def json_encode_value(self, value: Any) -> Any:
        return value


class TextComponentProperty(ComponentProperty):
    def __init__(self, identifier: str, description: str):
        super().__init__(identifier, description)

    def serialize(self) -> dict[str, Any]:
        return {
            **super().serialize(),
            "type": "text"
        }

    def validate0(self, value: Any) -> None:
        if not isinstance(value, str):
            raise ValueError()

    def json_encode_value(self, value: Any) -> Any:
        return value


class ChoiceComponentProperty(ComponentProperty):
    __choice_descriptions: dict[str, str]

    def __init__(self, identifier: str, description: str, choices: list[str | dict[str, str]]):
        super().__init__(identifier, description)

        self.__choice_descriptions = {}
        for choice in choices:
            if isinstance(choice, str):
                self.__choice_descriptions[choice] = choice
            elif isinstance(choice, dict):
                self.__choice_descriptions[choice['identifier']] = choice['description']

    def serialize(self) -> dict[str, Any]:
        return {
            **super().serialize(),
            "type": "choice",
            "choices": [
                {
                    "identifier": choice_identifier,
                    "description": choice_description
                } for (choice_identifier, choice_description) in self.choice_descriptions.items()
            ]
        }

    @property
    def choices(self) -> set[str]:
        return set(self.__choice_descriptions.keys())

    @property
    def choice_descriptions(self) -> dict[str, str]:
        return self.__choice_descriptions

    def validate0(self, value: Any) -> None:
        if value not in self.__choice_descriptions:
            raise ValueError()

    def json_encode_value(self, value: Any) -> Any:
        return value


class ConstantComponentProperty(ComponentProperty):
    __inner_property: ComponentProperty
    __value: Any

    def __init__(self, identifier: str, description: str, inner_property: ComponentProperty, value: Any):
        super().__init__(identifier, description)
        inner_property.validate(value)

        self.__inner_property = inner_property
        self.__value = value

    def serialize(self) -> dict[str, Any]:
        return {
            **super().serialize(),
            "type": "constant",
            "inner-property-spec": self.inner_property.serialize(),
            "value": self.json_encode_value(self.value)
        }

    def validate0(self, value: Any) -> None:
        if value != self.value:
            raise ValueError(f"Unexpected value, expected {self.value}, got {value}.")

    def json_encode_value(self, value: Any) -> Any:
        return self.inner_property.json_encode_value(value)

    @property
    def value(self) -> Any:
        return self.__value

    @property
    def inner_property(self):
        return self.__inner_property


class ComponentSpec:
    __identifier: str
    __properties: dict[str, ComponentProperty]

    def __init__(self, identifier: str, properties: list[ComponentProperty]):
        self.__identifier = normalize_identifier(identifier)
        self.__properties = {prop.identifier: prop for prop in properties}

    @staticmethod
    def parse_serialized(serialized: dict[str, Any]) -> "ComponentSpec":
        def parse_property(serialized_property: dict[str, Any]) -> ComponentProperty:
            identifier = normalize_identifier(serialized_property['identifier'])
            description = serialized_property['description']
            match serialized_property['type']:
                case "integer":
                    return IntegerComponentProperty(identifier, description)
                case "decimal":
                    return DecimalComponentProperty(identifier, description)
                case "text":
                    return TextComponentProperty(identifier, description)
                case "choice":
                    return ChoiceComponentProperty(identifier, description, serialized_property['possible-choices'])
                case "constant":
                    return ConstantComponentProperty(identifier, description, parse_property(serialized_property['inner-property-spec']), serialized_property['value'])
                case _:
                    raise ValueError(f"Unknown property type \"{serialized_property['type']}\".")

        properties = [parse_property(prop) for prop in serialized['properties']]
        return ComponentSpec(serialized['identifier'], properties)

    def serialize(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "properties": [
                prop.serialize() for prop in self.properties.values()
            ]
        }

    @property
    def identifier(self) -> str:
        return self.__identifier

    @property
    def properties(self) -> dict[str, ComponentProperty]:
        return self.__properties


class ProjectSpec:
    __components: dict[str, ComponentSpec]

    def __init__(self, components: list[ComponentSpec]):
        self.__components = {comp.identifier: comp for comp in components}

    @staticmethod
    def parse_serialized(serialized: dict[str, Any]) -> "ProjectSpec":
        return ProjectSpec([ComponentSpec.parse_serialized(component) for component in serialized['components']])

    def serialize(self) -> dict[str, Any]:
        return {
            "components": [component.serialize() for component in self.components.values()]
        }

    @property
    def components(self) -> dict[str, ComponentSpec]:
        return self.__components


class ExperimentSpec:
    __component: str
    __network: Network
    __mpspdz_protocol: protocol.Protocol
    __mpspdz_parties: int
    __mpspdz_batch_size: int
    __mpspdz_bits_from_squares: bool
    __component_properties: dict[str, Any]
    __number_of_exectutions: int
    __trusted: bool = False

    def __init__(self,
                 component: str,
                 network: Network,
                 mpspdz_protocol: protocol.Protocol,
                 mpspdz_parties: int,
                 mpspdz_batch_size: int,
                 mpspdz_bits_from_squares: bool,
                 component_properties: dict[str, Any],
                 number_of_executions: int,
                 trusted: bool = False):
        self.__component = component
        self.__network = network
        self.__mpspdz_protocol = mpspdz_protocol
        self.__mpspdz_parties = mpspdz_parties
        self.__mpspdz_batch_size = mpspdz_batch_size
        self.__mpspdz_bits_from_squares = mpspdz_bits_from_squares
        self.__component_properties = component_properties
        self.__number_of_exectutions = number_of_executions
        self.__trusted = trusted

    @staticmethod
    def parse_serialized(serialized: dict[str, Any]) -> "ExperimentSpec":
        network = Network(serialized['network']['delay'], serialized['network']['bandwidth-in'], serialized['network']['bandwidth-out'])
        number_of_executions = serialized['number-of-executions'] if 'number-of-executions' in serialized else 1

        # Find the protocol
        prot = None
        prot_name = serialized['mp-spdz']['protocol'].lower().replace('-', '')
        for name, val in protocol.__dict__.items():
            if not hasattr(val, "executable"):
                continue
            if name.lower() == prot_name:
                prot = val
                break
            elif getattr(val, "executable")[:-len("-party.x")].lower() == prot_name:
                prot = val
                break
        if prot is None:
            raise ValueError(f"Unknown protocol \"{serialized['mp-spdz']['protocol']}\".")

        return ExperimentSpec(
            normalize_identifier(serialized['component']),
            network,
            prot,
            serialized['mp-spdz']['n-parties'],
            serialized['mp-spdz']['batch-size'],
            serialized['mp-spdz']['bits-from-squares'],
            { normalize_identifier(identifier): value for (identifier, value) in serialized['component-properties'].items() },
            number_of_executions,
            trusted=serialized['trusted']
        )

    def serialize(self) -> dict[str, Any]:
        return {
            "component": self.component_identifier,
            "network": {
                "delay": self.network.delay,
                "bandwidth-in": self.network.incoming_bandwidth,
                "bandwidth-out": self.network.outgoing_bandwidth
            },
            "mp-spdz": {
                "protocol": self.mpspdz_protocol.executable[:-len("-party.x")],
                "n-parties": self.mpspdz_parties,
                "batch-size": self.mpspdz_batch_size,
                "bits-from-squares": self.mpspdz_bits_from_squares
            },
            "component-properties": self.component_properties,
            "number-of-executions": self.number_of_executions,
            "trusted": self.trusted
        }

    def validate_against(self, project_spec: ProjectSpec | None = None, component_spec: ComponentSpec | None = None):
        if component_spec is None:
            if project_spec is None:
                raise ValueError("At least a project or component specification must be provided.")
            if self.component_identifier not in project_spec.components:
                raise ValueError(f"Unkown component \"{self.component_identifier}\".")
            component_spec = project_spec.components[self.component_identifier]

        if self.mpspdz_parties < self.mpspdz_protocol.min_number_of_parties or self.mpspdz_parties > self.mpspdz_protocol.max_number_of_parties:
            raise ValueError("The protocol does not support the specified number of parties.")
        if self.mpspdz_batch_size < 1:
            raise ValueError("The batch size must be equal or larger to 1.")

        if not isinstance(self.number_of_executions, int):
            raise ValueError("Number of executions must be an integer.")

        # TODO: Validate network

        if component_spec.identifier != self.component_identifier:
            raise ValueError("Wrong component specification!")

        handled_properties = set()
        for (property_identifier, value) in self.component_properties.items():
            property_identifier = normalize_identifier(property_identifier)
            if property_identifier not in component_spec.properties:
                raise ValueError(f"Unknown property \"{property_identifier}\".")
            component_spec.properties[property_identifier].validate(value)
        unhandled_properties = set(component_spec.properties.keys()).difference(handled_properties)
        if len(handled_properties) > 0:
            raise ValueError(f"Missing properties: {unhandled_properties}")

    @property
    def component_identifier(self) -> str:
        return self.__component

    @property
    def component_properties(self) -> dict[str, Any]:
        return self.__component_properties

    @property
    def network(self) -> Network:
        return self.__network

    @property
    def mpspdz_protocol(self) -> protocol.Protocol:
        return self.__mpspdz_protocol

    @property
    def mpspdz_parties(self) -> int:
        return self.__mpspdz_parties

    @property
    def mpspdz_batch_size(self) -> int:
        return self.__mpspdz_batch_size

    @property
    def mpspdz_bits_from_squares(self) -> bool:
        return self.__mpspdz_bits_from_squares

    @property
    def number_of_executions(self) -> int:
        return self.__number_of_exectutions
    
    @property
    def trusted(self) -> bool:
        return self.__trusted