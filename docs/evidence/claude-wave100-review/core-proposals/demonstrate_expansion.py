"""String-only translation of the inspected Swift scanner; never invokes a shell."""
tokens = {'AGT_SELECTION': 'selected "quoted" text', 'AGT_SESSION_ID': 'session-1'}

def original(template):
    result = ''
    while '{' in template:
        start = template.index('{')
        result += template[:start]
        end = template.find('}', start + 1)
        if end == -1:
            return result + template[start:]
        result += tokens.get(template[start + 1:end], template[start:end + 1])
        template = template[end + 1:]
    return result + template

for source in ['printf \'%s\' "${AGT_SELECTION}"', '{ printf \'%s\' "{AGT_SESSION_ID}"; }']:
    print('input: ', repr(source))
    print('output:', repr(original(source)))
