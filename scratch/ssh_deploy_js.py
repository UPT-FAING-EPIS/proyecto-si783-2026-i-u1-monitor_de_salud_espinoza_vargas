import sys
import paramiko

def main():
    host = "38.250.116.71"
    port = 22
    username = "root"
    password = "upt2026"

    # Rutas locales
    local_js = r"c:\Users\Gerardo\Documents\GitHub\proyecto-si783-2026-i-u1-monitor_de_salud_espinoza_vargas\static\dashboard.js"
    local_html = r"c:\Users\Gerardo\Documents\GitHub\proyecto-si783-2026-i-u1-monitor_de_salud_espinoza_vargas\templates\index.html"

    # Rutas remotas
    remote_js = "/opt/monitor/static/dashboard.js"
    remote_html = "/opt/monitor/templates/index.html"

    print("Conectando a la VM...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=port, username=username, password=password, timeout=10)
        print("SSH Conectado!")
    except Exception as e:
        print(f"Error SSH: {e}")
        sys.exit(1)

    print("Iniciando SFTP para subir archivos...")
    try:
        sftp = ssh.open_sftp()
        sftp.put(local_js, remote_js)
        print(f"Subido: {remote_js}")
        sftp.put(local_html, remote_html)
        print(f"Subido: {remote_html}")
        sftp.close()
    except Exception as e:
        print(f"Error SFTP: {e}")
        ssh.close()
        sys.exit(1)

    print("Reiniciando servicio dbmonitor en la VM...")
    cmd = "systemctl restart dbmonitor && systemctl is-active dbmonitor"
    try:
        stdin, stdout, stderr = ssh.exec_command(cmd)
        out = stdout.read().decode('utf-8', errors='ignore').strip()
        print(f"Estado del servicio: {out}")
    except Exception as e:
        print(f"Error al reiniciar: {e}")

    ssh.close()
    print("Desplegado correctamente.")

if __name__ == "__main__":
    main()
