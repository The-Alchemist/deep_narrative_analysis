# Processing to create the Turtle rendering of the events for the sentences in a narrative
# The sentences are defined by their sentence dictionaries, which have the form:
#    'text': 'narrative_text',
#    'LOCS': ['location1', ...], 'TIMES': ['dates_or_times1', ...], 'EVENTS': ['event1', ...],
#    'subjects': [{'subject_text': 'subject_text', 'subject_type': 'type_such_as_SINGNOUN'},
#                 {'subject_text': 'Narrator', 'subject_type': 'example_FEMALESINGPERSON'}],
#    'verbs': [{'verb_text': 'verb_text', 'verb_lemma': 'verb_lemma', 'tense': 'tense_such_as_Past',
#               'preps': [{'prep_text': 'preposition_text',
#                          'prep_details': [{'detail_text': 'preposition_object', 'detail_type': 'type_eg_SINGGPE'}]}],
#                          # Preposition object may also have a preposition - for ex, 'with the aid of the police'
#                          # If so, following the 'detail_type' entry would be another 'preps' element
#                          'objects': [{'object_text': 'verb_object_text', 'object_type': 'type_eg_SINGNOUN'}]}]}]}]}]}

import copy
import logging
import uuid

from coreference_resolution import check_nouns
from create_event_time_loc import names_to_geo_dict, get_location_uri_and_ttl, \
    get_sentence_location, get_sentence_time
from database import query_database
from idiom_processing import get_verb_processing, process_idiom_detail
from nlp import get_head_noun, get_sentence_sentiment
from query_ontology_and_sources import get_noun_ttl, get_event_state_ttl
from utilities import domain_database, empty_string, objects_string, preps_string, \
    subjects_string, verbs_string, add_unique_to_array

query_domain = 'prefix : <urn:ontoinsights:dna:> SELECT ?uri ?type ?prob WHERE ' \
               '{ ?uri a ?type . ' \
               '{ { ?uri rdfs:label ?label . FILTER(?label = "keyword") . BIND(100 as ?prob) } UNION ' \
               '{ ?uri :noun_synonym ?nsyn . FILTER(?nsyn = "keyword") . BIND(100 as ?prob) } UNION ' \
               '{ ?uri rdfs:label ?label . FILTER(CONTAINS(?label, "keyword")) . BIND(90 as ?prob) } UNION ' \
               '{ ?class :noun_synonym ?nsyn . FILTER(CONTAINS(?nsyn, "keyword")) . BIND(90 as ?prob) } UNION ' \
               '{ ?uri rdfs:label ?label . FILTER(CONTAINS(lcase(?label), "keyword")) . BIND(85 as ?prob) } ' \
               'UNION { ?uri rdfs:label ?label . FILTER(CONTAINS("keyword", ?label)) . BIND(80 as ?prob) } } } ' \
               'ORDER BY DESC(?prob)'


# TODO: of someone (possession), of something (part), of related to measurement (2 lbs of potatoes)
# TODO: https://www.oxfordlearnersdictionaries.com/us/definition/english/of
# TODO: near loc, on loc, https://www.oxfordlearnersdictionaries.com/us/definition/english/over_1
# TODO: without x
# Date processing is handled differently/separately
prep_to_predicate_for_locs = {'about': ':has_topic',
                              'at': ':has_location',
                              'from': ':has_origin',
                              'to': ':has_destination',
                              'in': ':has_location'}


def check_domain_specific_match(noun: str, noun_type: str) -> (str, str):
    """
    Checks if the concept/Person/Location/... is already defined in the domain-specific ontology modules.

    :param noun: String holding the text to be matched
    :param noun_type: String holding the noun type (PERSON/GPE/LOC/...) from spacy's NER
    :returns: A tuple consisting of the matched URI and its type (if a match is found) or two empty strings
    """
    if noun_type.endswith('PERSON') or noun_type.endswith('GPE') or noun_type.endswith('LOC') \
            or noun_type.endswith('ORG') or noun_type.endswith('NORP') or noun_type.endswith('EVENT'):
        domain_details = query_database('select', query_domain.replace('keyword', noun), domain_database)
        if domain_details:
            return domain_details[0]['uri']['value'], domain_details[0]['type']['value']
    return empty_string, empty_string


def create_event_turtle(narr_gender: str, sentence_dicts: list) -> list:
    """
    Using the sentence dictionaries generated by the nlp_graph functions, create the Turtle
    rendering of the events.

    :param narr_gender: Either an empty string or one of the values, AGENDER, BIGENDER, FEMALE or MALE -
                        indicating the gender of the narrator
    :param sentence_dicts: An array of sentence dictionaries for a narrative
    :returns: A list of the Turtle statements
    """
    logging.info(f'Creating event Turtle')
    last_date = empty_string  # Track the last mentioned date/time/event
    # List of dates/times/specific events in the sentences
    processed_dates = []      # Track all dates/times/events mentioned in the Turtle to only define once
    last_loc = empty_string   # Track the last mentioned location
    # Track all locations mentioned in the Turtle to only define once
    processed_locs = dict()   # Keys are location strings and the values are their URIs
    last_nouns = []           # List of tuples (noun text and type) from previous sentence - Used for coref resolution
    graph_ttl_list = ['@prefix : <urn:ontoinsights:dna:> .', '@prefix dna: <urn:ontoinsights:dna:> .',
                      '@prefix geo: <urn:ontoinsights:geonames:> .',
                      '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .']
    for sent_dict in sentence_dicts:
        sentence_text = sent_dict['text']
        sentence_offset = sent_dict['offset']
        if not sentence_text[0].isalnum():
            # Parse can return a verb_text of punctuation or new line; Ignore this
            continue
        if sentence_text == 'New line':
            last_nouns = []         # Reset list of last_nouns (coref only selects from current paragraph)
            continue
        sent_keys = sent_dict.keys()
        new_loc = empty_string
        if 'LOCS' in sent_keys:
            new_loc = get_sentence_location(sent_dict, last_loc)
            if not last_loc:
                last_loc = new_loc
        if 'TIMES' in sent_keys or 'EVENTS' in sent_keys:
            # Format of last_date: ('before'|'after'|'') (PointInTime date)
            last_date, date_ttl = get_sentence_time(sent_dict, last_date, processed_dates)
            if date_ttl:
                graph_ttl_list.extend(date_ttl)
        subjects = []
        if subjects_string in sent_keys:
            # Get the subject nouns in the sent_dictionary and if a pronoun, attempt to resolve co-references
            subjects = check_nouns(narr_gender, sent_dict, subjects_string, last_nouns)
        sentence_ttl_list = []
        objects = []
        # Get xcomp processing details since this/these will be combined with the xcomp verb details
        # For example, 'she loved to play with her sister' => 'love' verb with the processing, "xcomp > :love, play"
        # And the 'play' verb with the prepositional details
        xcomp_dict = dict()
        for verb in sent_dict[verbs_string]:
            if 'verb_processing' in verb.keys() and 'xcomp >' in verb['verb_processing']:
                xcomp_verb = verb['verb_processing'].split(', ')[1].split(')')[0]
                xcomp_dict[xcomp_verb] = verb['verb_processing']
        for verb in sent_dict[verbs_string]:
            if 'verb_processing' in verb.keys() and 'xcomp >' in verb['verb_processing']:
                # Can ignore this verb since we already have the processing details
                continue
            if verb['verb_lemma'] in xcomp_dict.keys():    # Get the xcomp rule that was captured above
                # Since the rule is associated with the main verb, not the 'clausal' verb, which this is
                xcomp_processing = xcomp_dict[verb['verb_lemma']]
            else:
                xcomp_processing = empty_string
            event_uri, new_ttl_list, new_objects = \
                process_sentence_verb(narr_gender, sentence_text, sentence_offset, verb, xcomp_processing,
                                      subjects, last_date, last_loc, last_nouns, processed_locs)
            add_unique_to_array(new_objects, objects)       # Track all object nouns
            sentence_ttl_list.extend(new_ttl_list)
        graph_ttl_list.extend(sentence_ttl_list)
        # Update last_nouns to all the subjects/objects of the sentence
        add_unique_to_array(objects, subjects)   # Add objects -> subjects
        last_nouns = copy.deepcopy(subjects)     # Reset last_nouns to the nouns from the current sentence
        # Update the last_loc
        if new_loc:
            last_loc = new_loc
    # Finished with all the sentences
    return graph_ttl_list


# Functions internal to the module but accessible to testing
def create_ttl_for_prep_detail(processed_preps: list, event_uri: str, processed_locs: dict,
                               sentence_text: str, turtle: list) -> list:
    """
    Parse the details for a verb's prepositions and create the corresponding Turtle. Note that
    dates/times are not handled in this code, but for the sentence overall (in create_event_turtle).

    :param processed_preps: A list of tuples holding the preposition text, its object text
                            and object type
    :param event_uri: The URI for the verb/event
    :param processed_locs: A dictionary of location texts (keys) and their URI (values) of
                           all locations already processed
    :param sentence_text: The full text of the sentence (needed for checking for idioms)
    :param turtle: The current Turtle rendering of the sentence to which the preposition object
                   details are added
    :returns: A list holding the tuples of the preposition text, its object text and type, and the
             URI of the object
    """
    ttl_list = []
    new_tuples = []
    for prep in processed_preps:
        prep_text, obj_text, obj_type = prep
        obj_uri = f':{obj_text.replace(" ", "_")}'
        if obj_type.endswith('GPE'):
            if obj_text in names_to_geo_dict.keys():
                obj_uri = f'geo:{names_to_geo_dict[obj_text]}'
                ttl_list.append(
                    f'{event_uri} {prep_to_predicate_for_locs[prep_text]} {obj_uri} .')
                new_tuples.append((prep_text, obj_text, obj_type, obj_uri))
                continue
        # Else (not country)
        # Deal with locations first
        domain_uri, domain_type = check_domain_specific_match(obj_text, obj_type)
        if obj_type.endswith('GPE') or obj_type.endswith('LOC'):   # A location, so the processing is simpler
            if domain_uri:    # Already have the noun/concept defined
                ttl_list.append(
                    f'{event_uri} {prep_to_predicate_for_locs[prep_text]} <{domain_uri}> .')
                new_tuples.append((prep_text, obj_text, obj_type, domain_uri))
                continue
            # Get location details and add the corresponding Turtle
            loc_uri, loc_ttl = get_location_uri_and_ttl(obj_text, processed_locs)
            if loc_ttl:
                ttl_list.extend(loc_ttl)
            ttl_list.append(f'{event_uri} {prep_to_predicate_for_locs[prep_text]} {loc_uri} .')
            new_tuples.append((prep_text, obj_text, obj_type, loc_uri))
            continue
        if domain_uri:    # Already have the noun/concept defined
            ttl_list.append(f'{event_uri} {prep_to_predicate_for_locs[prep_text]} <{domain_uri}> .')
            new_tuples.append((prep_text, obj_text, obj_type, domain_uri))
            continue
        # Else (not a location and not already defined)
        elif not ('PERSON' in obj_type or obj_type.endswith('ORG') or obj_type.endswith('NORP')):
            # Some kind of thing or event that could be repeated
            obj_uri = f'{obj_uri}_{str(uuid.uuid4())[:13]}'   # Adjust the object URI to be unique
        noun_ttl = get_noun_ttl(obj_uri, obj_text, obj_type, sentence_text)
        ttl_list.extend(noun_ttl)
        new_tuples.append((prep_text, obj_text, obj_type, obj_uri))
        noun_str = str(noun_ttl)
        # Relationships for an Agent are different from associations for other "things"
        if ':Person' in noun_str or 'Agent' in noun_str or ':Organization' in noun_str:
            if prep_text == 'from':
                ttl_list.append(f'{event_uri} :has_provider {obj_uri} .')
            elif prep_text == 'to':
                ttl_list.append(f'{event_uri} :has_recipient {obj_uri} .')
            elif prep_text == 'for':
                ttl_list.append(f'{event_uri} :has_affected_agent {obj_uri} .')
            elif prep_text == 'with':
                ttl_list.append(f'{event_uri} :has_active_agent {obj_uri} .')
        else:
            if prep_text == 'with':
                ttl_list.append(f'{event_uri} :has_instrument {obj_uri} .')
            elif prep_text in prep_to_predicate_for_locs.keys() and ':Location' in noun_str:
                ttl_list.append(f'{event_uri} {prep_to_predicate_for_locs[prep_text]} {obj_uri} .')
            elif prep_text in ('about', 'of', 'from', 'to'):
                ttl_list.append(f'{event_uri} :has_topic {obj_uri} .')
    if ttl_list:
        turtle.extend(ttl_list)
    return new_tuples


def get_preposition_details(prep_dict: dict) -> list:
    """
    Extracts the details from the preposition dictionary of a verb.

    :param prep_dict: A dictionary holding the details for a single preposition for a verb
                      For example, "{'prep_text': 'with', 'prep_details': [{'detail_text': 'other children',
                      'detail_type': 'PLURALNOUN'}]}"
    :returns: An array holding tuples consisting of the preposition text, and the preposition's object
             text and object type
    """
    prep_details = []
    if 'prep_details' in prep_dict.keys():
        for prep_detail in prep_dict['prep_details']:
            if 'detail_text' in prep_detail.keys():
                prep_detail_type = prep_detail['detail_type']
                if prep_detail_type.endswith('DATE') or prep_detail_type.endswith('TIME') or  \
                        prep_detail_type.endswith('EVENT'):
                    # Time-related - so, this is already handled => ignore
                    continue
                prep_details.append((prep_dict['prep_text'].lower(), prep_detail['detail_text'],
                                     prep_detail['detail_type']))
    return prep_details


def handle_event_state_idiosyncrasies(event_state_turtle: str, subj_dict: dict,
                                      obj_tuples: list, obj_dict: dict, prep_tuples: list) -> str:
    """
    Handles subj (identified by the text, ':word_detail'), xcomp and  dobj/pobj references in a
    parsed idiom rule. These could be resolved in the idiom processing but are not since
    1) there is no difference (or efficiencies to be had) in the code and 2) the processing involves
    calling the get_event_state_ttl function, which would result in a circular reference from the
    idiom_processing module.

    At this time, subj and xcomp processing does not involve any object references.

    :param event_state_turtle: The Turtle statement resulting from processing an idiom rule
    :param subj_dict: A dictionary consisting the verb's subjects' texts (keys) and their associated URIs
                      (values)
    :param obj_tuples: An array of tuples consisting of the verb's objects text and type
    :param obj_dict: A dictionary consisting the verb's objects' texts (keys) and their associated URIs
                     (values)
    :param prep_tuples: An array of tuples consisting of the preposition's text, and its object text,
                        type and URI
    :returns: The updated Turtle statement if 'word_detail', 'dobj', 'pobj' or 'xcomp' text was present in the
             input event_state_turtle
    """
    if 'EnvironmentAndCondition' in event_state_turtle:   # Indicates that a subj_rule was parsed
        subj_uris = []
        for subj_text, subj_uri in subj_dict.items():
            subj_uris.append(subj_uri)
        uri_str = ', '.join(subj_uris)
        if 'subj :Ethnicity' in event_state_turtle:
            event_state_turtle = f'{event_state_turtle.split("; :word_detail")[0]}.'
            return event_state_turtle.replace(':Ethnicity', ':has_agent_aspect').replace('subj', uri_str)
        elif 'subj :PoliticalIdeology' in event_state_turtle:
            event_state_turtle = f'{event_state_turtle.split("; :word_detail")[0]}.'
            return event_state_turtle.replace(':PoliticalIdeology', ':has_political_ideology').\
                replace('subj', uri_str)
        elif 'subj :LineOfBusiness' in event_state_turtle:
            event_state_turtle = event_state_turtle.replace(':LineOfBusiness', ':has_line_of_business').\
                replace(':word_detail', ':line_of_business').replace('subj', uri_str)
            return f'{event_state_turtle} .'
        else:
            event_state_turtle = event_state_turtle.split(" subj")[0]
            return f'{event_state_turtle} {uri_str} .'
    if 'xcomp' in event_state_turtle:        # Appears as xcomp(verb1, verb2) or xcomp(verb1)
        if ', ' in event_state_turtle:
            verb1_text = event_state_turtle.split('(')[1].split(', ')[0]
            verb2_text = event_state_turtle.split(', ')[1].split(')')[0]
        else:
            verb1_text = event_state_turtle.split('(')[1].split(')')[0]
            verb2_text = empty_string
        verb1_class = get_event_state_ttl(verb1_text)        # Get the Event/State class for the text
        if verb2_text:
            verb2_class = get_event_state_ttl(verb2_text)    # Get the Event/State class for the text
            return f'{event_state_turtle.split("xcomp")[0]}{verb1_class} , {verb2_class} .'
        return f'{event_state_turtle.split("xcomp")[0]} {verb1_class} .'
    if 'dobj' in event_state_turtle:
        # Appears as dobj(agent), dobj(location), dobj(verb) or dobj (mutually exclusive)
        obj_uris = []
        for obj_text, obj_type in obj_tuples:
            if 'dobj(verb)' in event_state_turtle:
                # Results are formatted for the main use case and returns a complete triple statement
                # Need to just get the class name
                if ' ' in obj_text:
                    obj_text = get_head_noun(obj_text)
                obj_uris.append(get_event_state_ttl(obj_text))
            elif 'dobj(agent)' not in event_state_turtle and 'dobj(location)' not in event_state_turtle and \
                    'dobj(verb)' not in event_state_turtle:
                obj_uris.append(obj_dict[obj_text])
            elif _verify_uri_requirements(event_state_turtle, obj_type):
                obj_uris.append(obj_dict[obj_text])
        if obj_uris:
            uri_str = ', '.join(obj_uris)
            event_state_turtle = event_state_turtle.replace('dobj(verb)', uri_str).replace('dobj(agent)', uri_str).\
                replace('dobj(location)', uri_str).replace('dobj', uri_str)
        else:
            logging.warning(f'Dobj in processed idiom but not found in sentence, {sentence_text}')
    if 'pobj' in event_state_turtle:
        # Appears as pobj(agent), pobj(location), pobj(prep_xxx), pobj(prep_xxx)(agent | location)
        # or pobj (mutually exclusive)
        obj_uris = []
        if '(prep_' in event_state_turtle:
            prep_of_interest = event_state_turtle.split('(prep_')[1].split(')')[0]
            # Processing specific to a single preposition
            for preposition, obj_text, obj_type, obj_uri in prep_tuples:
                if preposition == prep_of_interest:
                    if f'pobj(prep_{prep_of_interest})(agent)' not in event_state_turtle and \
                            f'pobj(prep_{prep_of_interest})(location)' not in event_state_turtle:
                        obj_uris.append(obj_uri)
                    elif _verify_uri_requirements(event_state_turtle, obj_type):
                        obj_uris.append(obj_uri)
        else:
            prep_of_interest = ''
            for preposition, obj_text, obj_type, obj_uri in prep_tuples:
                if '(agent)' not in event_state_turtle and '(location)' not in event_state_turtle:
                    obj_uris.append(obj_uri)
                elif _verify_uri_requirements(event_state_turtle, obj_type):
                    obj_uris.append(obj_uri)
        if obj_uris:
            uri_str = ', '.join(obj_uris)
            event_state_turtle = event_state_turtle.replace(f'pobj(prep_{prep_of_interest})(agent)', uri_str).\
                replace(f'pobj(prep_{prep_of_interest})(location)', uri_str).\
                replace(f'pobj(prep_{prep_of_interest})', uri_str).\
                replace('pobj(agent)', uri_str).replace('pobj(location)', uri_str).replace('pobj', uri_str)
        else:
            logging.warning(f'Pobj in processed idiom but not found in sentence, {sentence_text}')
    if event_state_turtle[-1] != '.':
        event_state_turtle += ' .'
    return event_state_turtle


def process_sentence_verb(narr_gender: str, sentence_text: str, sentence_offset: int,
                          verb_dict: dict, xcomp_processing: str, subjects: list, last_date: str,
                          last_loc: str, last_nouns: list, processed_locs: dict) -> (str, list, list):
    """
    Generate the Turtle for the root event/verb of the sentence, based on the details for the verb
    in the sentence dictionary.

    :param narr_gender: Either an empty string or one of the values, AGENDER, BIGENDER, FEMALE or MALE -
                        indicating the gender of the narrator
    :param sentence_text: The full text of the sentence (needed for checking for idioms)
    :param sentence_offset: Integer indicating the order of the sentence in the overall narrative
    :param verb_dict: The verb details from the sentence dictionary
    :param xcomp_processing: An 'xcomp' idiom rule that should be applied when processing the
                             verb
    :param subjects: Array of tuples that are the subject text and its type
    :param last_date: The inferred (or explicit) time of the event, formatted as:
                      ('before'|'after'|'') (PointInTime date)
    :param last_loc: An inferred location of the event, if not supplanted by new info from the sentence
                     - OR - the origin location for a movement/transport
    :param last_nouns: A list of all noun text and type tuples that is used for co-reference resolution
                       (it is updated with new nouns from the verb prepositions)
    :param processed_locs: A dictionary of location texts (keys) and their URI (values) of
                           all locations already processed
    :returns: A tuple with 1) a string that is the event URI, 2) a list of Turtle statements describing
             the event, and 3) an array of tuples of the verb's object nouns and their types
    """
    logging.info(f'Processing verb, {verb_dict["verb_lemma"]}')
    ttl_list = []
    event_uri = f':Event_{str(uuid.uuid4())[:13]}'
    ttl_list.append(f'{event_uri} :text "{sentence_text}" ; :sentence_offset {sentence_offset} .')
    if 'negation' in verb_dict.keys():
        ttl_list.append(f'{event_uri} :negation true .')
    # Process the date of the event
    date_uri = f":{last_date.split(':')[1]}"   # Format of last_date: ('before'|'after'|'') (PointInTime date)
    if last_date.startswith('before'):
        ttl_list.append(f'{event_uri} :has_latest_end {date_uri} .')
    elif last_date.startswith('after'):
        ttl_list.append(f'{event_uri} :has_earliest_beginning {date_uri} .')
    else:
        ttl_list.append(f'{event_uri} :has_time {date_uri} .')
    verb_keys = verb_dict.keys()
    # Get co-reference details for objects
    objects = []
    if objects_string in verb_keys:
        # Check previous and current sentence nouns
        add_unique_to_array(check_nouns(narr_gender, verb_dict, objects_string, last_nouns), objects)
        # TODO: What about coref within a sentence? (Future)
    # Process subjects and objects
    subj_dict = _process_subjects_and_objects(subjects, sentence_text, ttl_list)
    obj_dict = _process_subjects_and_objects(objects, sentence_text, ttl_list)
    # Process the prepositions and their objects
    prepositions = []        # List of tuples (preposition text, object, object type)
    if preps_string in verb_keys:
        for prep_dict in verb_dict[preps_string]:
            # Get the preposition details
            prepositions.extend(get_preposition_details(prep_dict))
    # Get URIs and add Turtle for all the objects of the prepositions
    # TODO: Some of the prep objs are not used/referenced and could be skipped, or removed from the KG later
    prep_tuples = create_ttl_for_prep_detail(prepositions, event_uri, processed_locs, sentence_text, ttl_list)
    # Map verbs and related details to the ontology
    # Get idiom processing details
    if xcomp_processing:
        processing = [xcomp_processing]
    elif 'verb_processing' in verb_keys:
        processing = [verb_dict['verb_processing']]
    else:
        processing = get_verb_processing(verb_dict)
    # Either parse the idiom or try to match the event/state to the semantics of the ontology classes
    xcomp_label = empty_string
    if processing:
        event_state_ttl = process_idiom_detail(processing, sentence_text, verb_dict, prepositions)
        # Handle object (dobj, pobj) and xcomp verb resolution
        if 'xcomp' in str(event_state_ttl):
            xcomp_label = event_state_ttl[0].split('(')[1].split(')')[0]
            if ', ' in xcomp_label:
                xcomp_label = xcomp_label.replace(',', ' to')
        for es_ttl in event_state_ttl:
            es_ttl = f'{event_uri} a {es_ttl}'
            ttl_list.append(handle_event_state_idiosyncrasies(es_ttl, subj_dict, objects, obj_dict, prep_tuples))
    else:
        event_state_ttl = get_event_state_ttl(verb_dict['verb_lemma'])
        ttl_list.append(f'{event_uri} a {event_state_ttl} .')
    # Get the origin for a MovementTravelAndTransportation event
    # Origin is the location defined by the preposition 'from' OR the last location/from the previous sentence
    if ':Movement' in str(event_state_ttl) and preps_string in verb_dict.keys() and \
            "'from'" not in str(verb_dict[preps_string]):
        loc_uri, loc_ttl = get_location_uri_and_ttl(last_loc, processed_locs)
        # Will use the last location, if appropriate, and that Turtle is already defined - no need to use loc_ttl
        ttl_list.append(f'{event_uri} :has_origin {loc_uri} .')
    verb_label = verb_dict['verb_text']
    # Special case for the word, 'using'
    if 'verb_advcl' in verb_keys:
        advcl_text = str(verb_dict['verb_advcl'])
        if "'verb_lemma': 'use'" in advcl_text:
            inst_text = advcl_text.split("object_text': '")[1]
            inst_text2 = inst_text[:inst_text.index("',")]
            inst_type = advcl_text.split("object_type': '")[1]
            inst_type2 = inst_type[:inst_type.index("'}")]
            inst_uri = f':{inst_text2.replace(" ", "_")}_{str(uuid.uuid4())[:13]}'
            ttl_list.append(f'{event_uri} :has_instrument {inst_uri} .')
            inst_ttl = get_noun_ttl(inst_uri, inst_text2, inst_type2, sentence_text)
            ttl_list.extend(inst_ttl)
    # Final cleanup/completion of semantics
    if not processing or (processing and 'subj >' not in processing[0]):
        # Subject and objects already addressed in subj_rule processing
        for key, value in subj_dict.items():
            if ':Affiliation' in str(event_state_ttl):
                ttl_list.append(f'{event_uri} :affiliated_agent {value} .')
            else:
                ttl_list.append(f'{event_uri} :has_active_agent {value} .')
        for obj in objects:
            obj_text, obj_type = obj
            if 'PERSON' in obj_type or obj_type.endswith('GPE') or obj_type.endswith('ORG') \
                    or obj_type.endswith('NORP'):
                if ':Affiliation' in str(event_state_ttl):
                    ttl_list.append(f'{event_uri} :affiliated_with {obj_dict[obj_text]} .')
                else:
                    ttl_list.append(f'{event_uri} :has_affected_agent {obj_dict[obj_text]} .')
            else:
                ttl_list.append(f'{event_uri} :has_topic {obj_dict[obj_text]} .')
    # Make sure that there is a location
    ttl_str = str(ttl_list)
    location_ttl = f'{event_uri} :location'
    if not (location_ttl.replace('location', 'has_location') in ttl_str or
            location_ttl.replace('location', 'has_origin') in ttl_str or
            location_ttl.replace('location', 'has_destination') in ttl_str):
        # No location from the sentence dictionary, so use/persist the last location
        loc_uri, loc_ttl = get_location_uri_and_ttl(last_loc, processed_locs)
        # Will use the last location, if appropriate, and that Turtle is already defined - no need to check loc_ttl
        ttl_list.append(f'{event_uri} :has_location {loc_uri} .')
    # Finish up with the label and sentiment
    ttl_list.append(f'{event_uri} rdfs:label '
                    f'"{_create_verb_label(verb_label, xcomp_label, subj_dict, obj_dict, prepositions).strip()}" .')
    ttl_list.append(f'{event_uri} :sentiment {get_sentence_sentiment(sentence_text)} .')
    return event_uri, ttl_list, objects
    

# Functions internal to the module
def _create_verb_label(verb_text: str, xcomp_text: str, subj_dict: dict, obj_dict: dict,
                       prepositions: list) -> str:
    """
    Creates a summary of the parts of the sentence that were used in defining the Turtle output.

    :param verb_text: The base lemma of the main verb
    :param xcomp_text: The lemma of an xcomp clausal complement
    :param subj_dict: A dictionary of the verb subjects' text (keys) and associated URIs (values)
    :param obj_dict: A dictionary of the verb objects' text (keys) and associated URIs (values)
    :param prepositions: An array of tuples consisting of the preposition's text, and its object text and type
    :returns: A string holding the verb label
    """
    if subj_dict:
        subj_labels = subj_dict.keys()
    else:
        subj_labels = []
    if obj_dict:
        obj_labels = obj_dict.keys()
    else:
        obj_labels = []
    if not subj_labels:
        event_label = f'{", ".join(obj_labels)} {xcomp_text}' if xcomp_text else \
            f'{", ".join(obj_labels)} {verb_text}'
    elif not obj_labels:
        event_label = f'{", ".join(subj_labels)} {xcomp_text}' if xcomp_text else \
            f'{", ".join(subj_labels)} {verb_text}'
    else:
        event_label = f'{", ".join(subj_labels)} {xcomp_text} {", ".join(obj_labels)}' \
            if xcomp_text else f'{", ".join(subj_labels)} {verb_text} {", ".join(obj_labels)}'
    for prep in prepositions:
        prep_text, obj_text, obj_type = prep
        if not prep_text:
            continue
        event_label += f' {prep_text} {obj_text}'
    return event_label


def _process_subjects_and_objects(nouns: list, sentence_text: str, turtle: list) -> dict:
    """
    Iterates through each of the noun tuples and either matches their text to a domain concept or
    creates the appropriate Turtle to describe their semantics. As part of the Turtle definition, an
    IRI identifying the noun is created.
    
    :param nouns: Array of tuples that are the noun text and type for subjects or objects
    :param sentence_text: The raw text of the sentence being processed
    :param turtle: The current array of Turtle statements (which is updated in this function, if the
                   noun is 'new' (not already defined in the domain)
    :returns: A dictionary of the noun text (keys) and their URI identifiers (values)
    """
    noun_dict = dict()
    for noun in nouns:
        noun_text, noun_type = noun
        noun_uri = f':{noun_text.replace(" ", "_")}'
        domain_uri, domain_type = check_domain_specific_match(noun_text, noun_type)
        if domain_uri:
            noun_uri = f'<{domain_uri}>'
        else:
            if not ('PERSON' in noun_type or noun_type.endswith('GPE') or noun_type.endswith('ORG') or 
                    noun_type.endswith('NORP')):
                # Some kind of thing or event that could be repeated
                noun_uri = f'{noun_uri}_{str(uuid.uuid4())[:13]}'   # Adjust the subject URI to be unique
            noun_ttl = get_noun_ttl(noun_uri, noun_text, noun_type, sentence_text)
            turtle.extend(noun_ttl)
        noun_dict[noun_text] = noun_uri
    return noun_dict


def _verify_uri_requirements(event_state_turtle: str, noun_type: str) -> bool:
    """
    Verifies if a noun type meets the requirements of a dobj or pobj reference in a parsed idiom rule.

    :param event_state_turtle: The Turtle statement resulting from processing an idiom rule
    :param obj_type: The type of the object/noun as defined by the spaCy parse
    :returns: A boolean indicating that
    """
    if '(agent)' in event_state_turtle and \
            ('PERSON' in noun_type or 'ORG' in noun_type or 'NORP' in noun_type or 'GPE' in noun_type):
        return True
    elif '(location)' in event_state_turtle and \
            ('GPE' in noun_type or 'LOC' in noun_type or 'FAC' in noun_type):
        return True
    return False
