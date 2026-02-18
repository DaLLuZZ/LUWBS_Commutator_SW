import env
import time
import pyvisa

def main():
    rm = pyvisa.ResourceManager()

    #print(f"Available ::INSTR (default) resources: {rm.list_resources()}")
    #print(f"Available ::SOCKET resources: {rm.list_resources('?*::SOCKET')}")

    unit_test_conn_disconn(rm)
    unit_test_idn_query(rm)

# helpers

def print_test_prologue(test_name):
    print(f"\nRunning test \"{test_name}\"")

def print_test_epilogue(test_name, error = None):
    if (error is None):
        print(f"\"{test_name}\" passed\n")
    else:
        print(f"\"{test_name}\" failed with error: {error}\n")

def resource_connect(rm):
    print(f"Connecting to {env.VISA_RESOURCE_NAME}")
    inst = rm.open_resource(env.VISA_RESOURCE_NAME, read_termination = "\r\n")
    inst.query_delay = env.VISA_QUERY_DELAY_SEC
    time.sleep(env.VISA_QUERY_DELAY_SEC)
    return inst

def resource_disconnect(inst):
    print(f"Disconnecting from {env.VISA_RESOURCE_NAME}")
    inst.close()

# unit tests

# connect and disconnect
def unit_test_conn_disconn(rm, test_name = "Connect & disconnect test"):
    print_test_prologue(test_name)
    inst = resource_connect(rm)
    resource_disconnect(inst)
    print_test_epilogue(test_name)

# IDN query
def unit_test_idn_query(rm, test_name = "IDN query test"):
    print_test_prologue(test_name)
    inst = resource_connect(rm)
    idn = inst.query('*IDN?')
    print(f"*IDN? -> {idn}")
    inst.close()
    print_test_epilogue(test_name)

if __name__ == '__main__':
    main()
