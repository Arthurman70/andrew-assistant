"""Generate private pairing material and Raspberry Pi cloud-init provisioning."""
import base64
import datetime as dt
import ipaddress
import json
from pathlib import Path
import secrets
import socket
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
PC_IP = ''


def main():
    global PC_IP
    import argparse
    parser=argparse.ArgumentParser(description='Create private Pi pairing files; never publish these files.')
    parser.add_argument('--pc-ip',required=True,help='Host PC local Ethernet IPv4 address')
    args=parser.parse_args();address=ipaddress.ip_address(args.pc_ip)
    if not address.is_private or address.is_loopback or address.version!=4:
        raise ValueError('Use the PC local Ethernet IPv4 address.')
    PC_IP=str(address)
    if (DATA/'server.crt').exists():
        existing=x509.load_pem_x509_certificate((DATA/'server.crt').read_bytes())
        if address not in existing.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress):
            raise ValueError('Existing pairing uses another PC address. Keep it or remove the old pairing manually before generating new keys.')
    token_file = DATA / 'relay-token.txt'
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(48))
    if not (DATA / 'server.key').exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Andrew PC relay')])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-dt.timedelta(minutes=10)).not_valid_after(now+dt.timedelta(days=730))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(PC_IP)),
                    x509.IPAddress(ipaddress.ip_address('127.0.0.1')), x509.DNSName('localhost'),
                    x509.DNSName(socket.gethostname()), x509.DNSName(socket.gethostname()+'.local')]), critical=False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .sign(key, hashes.SHA256()))
        (DATA / 'server.key').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (DATA / 'server.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    ssh = DATA / 'pi_ed25519'
    if not ssh.exists():
        key = ed25519.Ed25519PrivateKey.generate()
        ssh.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()))
        (DATA / 'pi_ed25519.pub').write_bytes(key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH))
    config = {'pc_url': 'https://' + PC_IP + ':8766', 'token': token_file.read_text().strip(),
              'microphone_device': 'auto', 'camera_device': '/dev/video0', 'pi_speaker': True,
              'speaker_device': 'auto'}
    (ROOT / 'pi/relay-config.json').write_text(json.dumps(config, indent=2))
    files = []
    for source, destination, mode in [('pi/agent.py', '/opt/andrew/agent.py', '0644'),
            ('wake_detector.py','/opt/andrew/wake_detector.py','0644'),
            ('wake_capture.py','/opt/andrew/wake_capture.py','0644'),
            ('background_guard.py','/opt/andrew/background_guard.py','0644'),
            ('pi/camera_capture.py','/opt/andrew/camera_capture.py','0644'),
            ('audio_utils.py','/opt/andrew/audio_utils.py','0644'),
            ('interruption.py','/opt/andrew/interruption.py','0644'),
            ('pi/relay-config.json', '/opt/andrew/relay-config.json', '0600'),
            ('data/server.crt', '/opt/andrew/pc.crt', '0644'),
            ('pi/install.sh', '/opt/andrew/install.sh', '0755'),
            ('pi/install-home.sh', '/opt/andrew/install-home.sh', '0755'),
            ('pi/install-music.sh', '/opt/andrew/install-music.sh', '0755'),
            ('pi/install-screen.sh', '/opt/andrew/install-screen.sh', '0755'),
            ('pi/bootstrap.sh', '/opt/andrew/bootstrap.sh', '0755'),
            ('pi/andrew-bootstrap.service', '/etc/systemd/system/andrew-bootstrap.service', '0644'),
            ('pi/screen.py', '/opt/andrew/screen.py', '0644'),
            ('pi/display.py','/opt/andrew/display.py','0644'),
            ('assets/andrew.png','/opt/andrew/andrew.png','0644'),
            ('pi/screen.html', '/opt/andrew/screen.html', '0644'),
            ('pi/andrew-screen.service', '/etc/systemd/system/andrew-screen.service', '0644'),
            ('pi/andrew-relay.service', '/etc/systemd/system/andrew-relay.service', '0644')]:
        content = (ROOT / source).read_bytes()
        if not source.endswith('.png'): content=content.replace(b'\r\n', b'\n')
        files.append({'path': destination, 'permissions': mode, 'encoding': 'b64', 'content': base64.b64encode(content).decode()})
    cloud = {'hostname': 'andrew-pi', 'manage_etc_hosts': True, 'timezone': 'America/New_York',
             'locale': 'en_US.UTF-8', 'enable_ssh': True, 'ssh_pwauth': False,
             'users': [{'name': 'andrew', 'groups': ['adm', 'sudo', 'audio', 'video', 'plugdev', 'input', 'render', 'netdev'],
                        'shell': '/bin/bash', 'sudo': 'ALL=(ALL) NOPASSWD:ALL', 'lock_passwd': True,
                        'ssh_authorized_keys': [(DATA / 'pi_ed25519.pub').read_text()]}],
             'write_files': files,
             'runcmd': [['systemctl', 'enable', 'ssh'],
                        ['systemctl', 'start', '--no-block', 'ssh'],
                        ['systemctl', 'daemon-reload'],
                        ['systemctl', 'enable', 'andrew-bootstrap.service'],
                        ['systemctl', 'start', '--no-block', 'andrew-bootstrap.service']]}
    # JSON is a YAML subset accepted by cloud-init; no quoting ambiguities.
    (ROOT / 'pi/user-data').write_text('#cloud-config\n' + json.dumps(cloud, indent=2) + '\n')
    # Raspberry Pi OS passes this file through to Netplan, including its network key.
    (ROOT / 'pi/network-config').write_text('network:\n  version: 2\n  renderer: NetworkManager\n  ethernets:\n    eth0:\n      dhcp4: true\n      optional: true\n')
    print('TLS pairing, SSH key and Pi first-boot configuration prepared. No camera was opened.')


if __name__ == '__main__':
    main()
