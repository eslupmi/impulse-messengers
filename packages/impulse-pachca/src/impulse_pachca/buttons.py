buttons = {
    'chain': {
        'takeit': {'text': 'Take It', 'data': 'stop_chain'},
        'release': {'text': 'Release', 'data': 'start_chain'},
    },
    'freeze': {
        'inactive': {'text': 'Freeze', 'data': 'freeze_menu'},
        'inhibited': {'text': 'Inhibited', 'data': 'noop'},
        'options': [
            {'text': 'Tomorrow', 'data': 'freeze_tomorrow'},
            {'text': 'Next Monday', 'data': 'freeze_next_monday'},
            {'text': 'In Month', 'data': 'freeze_month'},
            {'text': 'In 6 months', 'data': 'freeze_6months'},
            {'text': '« Back', 'data': 'freeze_back'},
        ],
    },
    'task': {
        'create': {'text': '📌', 'data': 'task'},
    },
}
