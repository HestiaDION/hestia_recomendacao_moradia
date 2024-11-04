from flask import Flask, request, jsonify
from pymongo import MongoClient
import logging
import uuid
from bson import Binary
from os import getenv
from dotenv import load_dotenv
from flasgger import Swagger, swag_from

app = Flask(__name__)

# Configuração do Swagger
swagger_template = {
    "swagger": "2.0",
    "info": {
        "title": "API de Recomendações de Moradias",
        "description": "API para obter recomendações de moradias com base no UUID universitário fornecido.",
        "version": "1.0.0"
    },
    "basePath": "/",  # Base path para a API
    "schemes": [
        "http",
        "https"
    ],
}

swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": 'apispec',
            "route": '/apispec.json',
            "rule_filter": lambda rule: True,  # Inclui todas as rotas
            "model_filter": lambda tag: True,  # Inclui todos os modelos
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/docs/"
}

swagger = Swagger(app, template=swagger_template, config=swagger_config)

# Configuração do logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

try:
    client = MongoClient(getenv('URI_MONGODB'))
    db = client[getenv('MONGO_DBNAME')]
    collection = db[getenv('MONGO_COLLECTION')]
    logger.info("Conexão com o MongoDB estabelecida com sucesso.")
except Exception as e:
    logger.error(f"Erro ao conectar ao MongoDB: {e}")
    raise

def get_filters(uuid_str: str) -> dict:
    """
    Busca um usuário na coleção com base no UUID fornecido e retorna um dicionário de filtros extraídos de campos específicos.

    Args:
        uuid_str (str): O UUID do usuário no formato padrão (ex: '592f7f4a-ebd2-4b3a-7e46-7e1af20de594').

    Returns:
        dict: Um dicionário onde cada chave é um dos campos especificados e cada valor é uma lista de filtros.
    """
    global collection
    logger.info(f"Obtendo filtros para o UUID: {uuid_str}")

    # Tentar converter a string UUID para um objeto UUID
    try:
        uuid_obj = uuid.UUID(uuid_str)
        logger.debug(f"UUID convertido com sucesso: {uuid_obj}")
    except ValueError:
        logger.error("UUID inválido fornecido.")
        return {}
    
    # Converter o objeto UUID para Binary com subtype=4
    binary_uuid = Binary(uuid_obj.bytes, subtype=4)
    
    # Realizar a busca no MongoDB
    try:
        user = collection.find_one({'idUsuarioMoradia': binary_uuid})
        if not user:
            logger.warning("Nenhum usuário encontrado com o UUID fornecido.")
            return {}
        logger.info("Usuário encontrado com sucesso.")
    except Exception as e:
        logger.error(f"Erro ao buscar usuário no MongoDB: {e}")
        return {}
    
    # Lista de campos a serem processados (removido 'preferencias_moveis_outro')
    fields_to_process = [
        'animais_estimacao',
        'preferencia_genero',
        'numero_maximo_pessoas',
        'frequencia_fumo',
        'frequencia_bebida'
    ]
    
    filters = {}
    for field in fields_to_process:
        value = user.get(field)
        if isinstance(value, str):
            value = value.replace('[', '').replace(']', '')
        filters[field] = value
        logger.debug(f"Filtro extraído - {field}: {value}")
    
    logger.info("Filtros obtidos com sucesso.")
    return filters

def calculate_match_percentage(housing_filters: dict, university_filters: dict) -> float:
    """
    Calcula o percentual de correspondência entre dois conjuntos de filtros, aplicando regras especiais.

    Args:
        housing_filters (dict): Dicionário de filtros do usuário de moradia.
        university_filters (dict): Dicionário de filtros do usuário universitário.

    Returns:
        float: Percentual de correspondência entre os dois conjuntos de filtros.
    """
    logger.info("Calculando o percentual de correspondência.")
    try:
        # Inicializa variáveis
        total_filters = 0
        matched_filters = 0

        # Verifica se algum dos conjuntos de filtros contém 'Alergia' ou ['Gosto muito', 'Não tenho, mas amo'] em 'animais_estimacao'
        animals_intersection = housing_filters.get('animais_estimacao', []) + university_filters.get('animais_estimacao', [])

        if 'Alergia' in animals_intersection:
            logger.info("Alergia detectada nas preferências de animais. Retornando 0%.")
            return 0.0
        elif 'Gosto muito' in animals_intersection or 'Não tenho, mas amo' in animals_intersection:
            total_filters += 1
            matched_filters += 1
            housing_filters.pop('animais_estimacao', None)
            university_filters.pop('animais_estimacao', None)
            logger.debug("Condição especial para 'Gosto muito' ou 'Não tenho, mas amo' aplicada.")

        # Verifica se algum dos conjuntos de filtros contém 'Tanto faz' em 'preferencia_genero'
        gender_intersection = [
            housing_filters.get('preferencia_genero', ''),
            university_filters.get('preferencia_genero', '')
        ]

        if 'Tanto faz' in gender_intersection:
            total_filters += 1
            matched_filters += 1
            housing_filters.pop('preferencia_genero', None)
            university_filters.pop('preferencia_genero', None)
            logger.debug("Condição especial para 'Tanto faz' em preferencia_genero aplicada.")

        # Comparação de número máximo de pessoas
        try:
            housing_max = int(housing_filters.get('numero_maximo_pessoas', 0))
            university_max = int(university_filters.get('numero_maximo_pessoas', 0))
            if housing_max <= university_max:
                matched_filters += 1
                logger.debug(f"numero_maximo_pessoas: {housing_max} <= {university_max}. Incrementando matched_filters.")
        except ValueError as ve:
            logger.error(f"Erro na conversão de numero_maximo_pessoas: {ve}")

        total_filters += 1
        housing_filters.pop('numero_maximo_pessoas', None)
        university_filters.pop('numero_maximo_pessoas', None)

        # Processa os demais campos não arrays
        other_fields = [
            'preferencia_genero',
            'frequencia_fumo',
            'frequencia_bebida'
        ]

        for field in other_fields:
            if housing_filters.get(field) == university_filters.get(field):
                matched_filters += 1
                logger.debug(f"Campo '{field}' corresponde. Incrementando matched_filters.")
            total_filters += 1

        # Evita divisão por zero
        if total_filters == 0:
            logger.warning("Total de filtros é zero. Retornando 0%.")
            return 0.0

        # Calcula o percentual
        match_percentage = (matched_filters / total_filters) * 100
        logger.info(f"Percentual de correspondência calculado: {match_percentage}%")
        return match_percentage
    except Exception as e:
        logger.error(f"Ocorreu um erro ao calcular a correspondência: {e}")
        return 0.0

def get_all_houses() -> list:
    """
    Retorna uma lista de UUIDs dos documentos na coleção onde o campo 'tipo' é 'moradia'.

    Utiliza a variável global 'collection' para realizar a consulta no banco de dados MongoDB.

    Returns:
        list: Uma lista contendo os UUIDs (em formato de string) dos documentos que correspondem ao tipo 'moradia'.
    """
    global collection
    logger.info("Obtendo todas as moradias da coleção.")

    try:
        # Realiza a busca na coleção por documentos com 'tipo' igual a 'moradia'
        cursor = collection.find({'tipo': 'moradia'}, {'idUsuarioMoradia': 1, '_id': 0})
        logger.debug("Consulta MongoDB realizada com sucesso.")

        uuid_list = []
        for document in cursor:
            binary_uuid = document.get('idUsuarioMoradia')
            if binary_uuid:
                # Converte o campo Binary para um objeto UUID
                try:
                    uuid_obj = uuid.UUID(bytes=binary_uuid)
                    uuid_str = str(uuid_obj)
                    uuid_list.append(uuid_str)
                    logger.debug(f"UUID adicionado: {uuid_str}")
                except (ValueError, TypeError) as e:
                    logger.error(f"Erro ao converter UUID: {e}")
        
        logger.info(f"Total de moradias encontradas: {len(uuid_list)}")
        return uuid_list

    except Exception as e:
        logger.error(f"Ocorreu um erro ao buscar as moradias: {e}")
        return []

def get_all_probas(university_uuid: str) -> list:
    """
    Retorna uma lista de UUIDs das moradias ordenadas de forma decrescente com base nas probabilidades de correspondência.

    A função realiza os seguintes passos:
    1. Obtém todas as moradias disponíveis.
    2. Calcula a probabilidade de correspondência entre as preferências da moradia e as do universitário.
    3. Cria um objeto com o UUID e a probabilidade.

    Args:
        university_uuid (str): O UUID do universitário no formato padrão (ex: '592f7f4a-ebd2-4b3a-7e46-7e1af20de594').

    Returns:
        list: Uma lista de objetos contendo UUIDs das moradias e suas respectivas probabilidades.
    """
    logger.info(f"Iniciando cálculo de probabilidades para o UUID universitário: {university_uuid}")
    try:
        houses = get_all_houses()
        houses_to_return = []

        for house_uuid in houses:
            housing_filters = get_filters(house_uuid)
            university_filters = get_filters(university_uuid)
            probability = calculate_match_percentage(housing_filters, university_filters)
            logger.debug(f"Probabilidade para a moradia {house_uuid}: {probability}%")
            
            house = {"uid": house_uuid, "probability": probability}

            houses_to_return.append(house)
            logger.info(f"Moradia {house_uuid} adicionada com probabilidade {probability}%")

        # Ordena as moradias de forma decrescente com base nas probabilidades
        logger.info("Moradias ordenadas com sucesso.")

        return houses_to_return

    except Exception as e:
        logger.error(f"Ocorreu um erro ao calcular as probabilidades: {e}")
        return []


@app.route('/recommended-homes', methods=['POST'])
@swag_from({
    'tags': ['Recomendações de Moradias'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'university_uuid': {
                        'type': 'string',
                        'description': 'UUID do universitário no formato padrão (ex: "592f7f4a-ebd2-4b3a-7e46-7e1af20de594")'
                    }
                },
                'required': ['university_uuid']
            }
        }
    ],
    'responses': {
        200: {
            'description': 'Requisição bem-sucedida.',
            'schema': {
                'type': 'object',
                'properties': {
                    'message': {
                        'type': 'string'
                    },
                    'houses': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'uid': {'type': 'string'},
                                'percentage': {'type': 'number'}
                            }
                        }
                    }
                }
            }
        },
        400: {
            'description': 'Erro de requisição inválida.',
            'schema': {
                'type': 'object',
                'properties': {
                    'error': {'type': 'string'}
                }
            }
        },
        415: {
            'description': 'Tipo de mídia não suportado.',
            'schema': {
                'type': 'object',
                'properties': {
                    'error': {'type': 'string'}
                }
            }
        },
        500: {
            'description': 'Erro interno do servidor.',
            'schema': {
                'type': 'object',
                'properties': {
                    'error': {'type': 'string'},
                    'detalhes': {'type': 'string'}
                }
            }
        }
    }
})
def recommended_homes():
    if not request.is_json:
        return jsonify({'error': 'Content-Type deve ser application/json.'}), 415
    data = request.get_json()
    university_uuid = data.get('university_uuid')
    if not university_uuid:
        return jsonify({'error': 'O campo "university_uuid" é obrigatório.'}), 400
    try:
        uuid.UUID(university_uuid)
    except ValueError:
        return jsonify({'error': 'O "university_uuid" fornecido não é um UUID válido.'}), 400
    houses = get_all_probas(university_uuid)
    return jsonify({'message': 'Requisição recebida com sucesso.', 'houses': houses}), 200

if __name__ == '__main__':
    # Executa a aplicação Flask na porta 5000 com debug ativado
    app.run(debug=True, host='0.0.0.0', port=int(getenv("PORT", 5000)))

