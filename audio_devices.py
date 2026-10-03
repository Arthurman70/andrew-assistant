"""Fresh Windows audio inventory. Run in a short-lived process after hotplug."""
import json
import sounddevice as sd


def inventory():
    devices = sd.query_devices()
    # Windows MME provides user-facing endpoints, unlike stale kernel-streaming pins.
    mme = next((i for i, api in enumerate(sd.query_hostapis()) if api['name'] == 'MME'), None)
    result = {'inputs': [], 'outputs': [], 'default_input': '', 'default_output': ''}
    virtual = ('virt', 'oculusvad', 'steam streaming', 'sound mapper', 'primary sound', 'stereo mix')
    for i, device in enumerate(devices):
        if mme is not None and device['hostapi'] != mme:
            continue
        if any(word in device['name'].lower() for word in virtual):
            continue
        entry = {'id': i, 'name': device['name']}
        if device['max_input_channels']:
            result['inputs'].append(entry)
            if i == sd.default.device[0]: result['default_input'] = device['name']
        if device['max_output_channels']:
            result['outputs'].append(entry)
            if i == sd.default.device[1]: result['default_output'] = device['name']
    return result


if __name__ == '__main__':
    print(json.dumps(inventory()))
