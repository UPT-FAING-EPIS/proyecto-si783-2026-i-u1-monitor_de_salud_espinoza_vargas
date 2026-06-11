import sys
import paramiko

def main():
    host = "38.250.116.71"
    port = 22
    username = "root"
    password = "upt2026"

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=port, username=username, password=password, timeout=10)
    except Exception as e:
        print(f"Error al conectar por SSH: {e}")
        sys.exit(1)

    # Ejecutamos una prueba de conexión simulando las credenciales correctas en la VM
    python_cmd = 'cd /opt/monitor && .venv/bin/python -c "import db_connection; ds = {\'tipo_db\': \'postgresql\', \'host\': \'127.0.0.1\', \'puerto\': 5432, \'usuario\': \'monitor\', \'password\': \'Monitor2026!@#\', \'database\': \'db_health_monitor\'}; print(db_connection.test_datasource(ds))"'
    
    try:
        stdin, stdout, stderr = ssh.exec_command(python_cmd)
        out = stdout.read().decode('utf-8', errors='ignore')
        err = stderr.read().decode('utf-8', errors='ignore')
        if out.strip():
            print("--- Resultado del test ---")
            print(out.strip())
        if err.strip():
            print("--- Errores ---")
            print(err.strip())
    except Exception as e:
        print(f"Error: {e}")

    ssh.close()

if __name__ == "__main__":
    main()
