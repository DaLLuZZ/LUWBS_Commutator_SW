# Constants used by each module

# Delay in seconds between write and read operations
VISA_QUERY_DELAY_SEC   = 0

# Delay after connect (before any operation)
VISA_CONNECT_DELAY_SEC = 1.0

# Resource name used as pyvisa.ResourceManager().open_resource() argument
VISA_RN_INT_TYPE       = "TCPIP"          # interface type
VISA_RN_HOST_IP        = "127.0.0.1"      # ip
VISA_RN_HOST_PORT      = "5025"           # scpi server port
VISA_RN_CLASS          = "SOCKET"         # "INSTR" / "SOCKET"

VISA_RESOURCE_NAME = VISA_RN_INT_TYPE + "::" + VISA_RN_HOST_IP + "::" + VISA_RN_HOST_PORT + "::" + VISA_RN_CLASS
