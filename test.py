import env
import time
import pyvisa
import itertools

def main():
    rm = pyvisa.ResourceManager()

    print(f"Available ::INSTR (default) resources: {rm.list_resources()}")
    print(f"Available ::SOCKET resources: {rm.list_resources('?*::SOCKET')}")

    unit_test_conn_disconn(rm)
    unit_test_idn_query(rm)

    unit_test_route_open(rm)
    unit_test_route_close_valid(rm)
    unit_test_route_close_invalid(rm)
    unit_test_route_close_enum_valid(rm)
    unit_test_route_closeq_range_valid(rm)
    unit_test_route_open(rm)

# common helpers

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
    time.sleep(env.VISA_CONNECT_DELAY_SEC)
    return inst

def resource_disconnect(inst):
    print(f"Disconnecting from {env.VISA_RESOURCE_NAME}")
    inst.close()

# Helper function to verify channel states
def verify_channel_states(inst, test_name, closed_channels):
    """
    Verify that specified channels are closed and all others are open.
    Args:
        inst: pyvisa Resource object for current running test
        test_name: String containing the name of current running test
        closed_channels: List of channels that should be closed (format: '@X!Y')
    Returns:
        bool: True if all channel states are as expected, False otherwise
    """
    closed_set = set(closed_channels)
    # Check all 16 possible channels in 4x4 matrix
    for input_num in range(4):
        for output_num in range(4):
            channel = f"@{input_num}!{output_num}"
            is_closed = channel in closed_set
            # Check CLOSe:STATe? - 1 if closed, 0 if open
            state = inst.query(f'ROUTe:CLOSe:STATe? ({channel})')
            expected_state = '1' if is_closed else '0'
            if state.strip() != expected_state:
                print_test_epilogue(test_name, 
                    f'Failed: Channel {channel} should be {"closed" if is_closed else "open"}, '
                    f'but ROUTe:CLOSe:STATe? returned "{state}"')
                return False
            # Check OPEN? - 0 if closed, 1 if open
            state = inst.query(f'ROUTe:OPEN? ({channel})')
            expected_state = '0' if is_closed else '1'
            if state.strip() != expected_state:
                print_test_epilogue(test_name, 
                    f'Failed: Channel {channel} should be {"closed" if is_closed else "open"}, '
                    f'but ROUTe:OPEN? returned "{state}"')
                return False
    return True

# Helper function to verify channel states (with enum argument)
def verify_channel_states_enum(inst, test_name, closed_channels):
    """
    Verify that specified channels are closed and all others are open.
    Args:
        inst: pyvisa Resource object for current running test
        test_name: String containing the name of current running test
        closed_channels: List of channels that should be closed (format: 'X!Y')
    Returns:
        bool: True if all channel states are as expected, False otherwise
    """
    closed_set = set(closed_channels)
    # Check all 16 possible channels in 4x4 matrix
    channels = []
    is_closed = []
    for input_num in range(4):
        for output_num in range(4):
            channels.append(f"{input_num}!{output_num}")
    for channel in channels:
        is_closed.append(channel in closed_set)
    chan_lst = "(@"
    for ch in channels:
        chan_lst += ch + ","
    chan_lst = chan_lst[0:len(chan_lst)-1] # remove last comma
    chan_lst = chan_lst + ")"
    cmp_str = ""
    for ic in is_closed:
        if ic is True:
            cmp_str += "1"
        else:
            cmp_str += "0"
        cmp_str += ","
    cmp_str = cmp_str[0:len(cmp_str)-1] # remove last comma
    res = inst.query(f'ROUTe:CLOSe:STATe? {chan_lst}')
    if (res != cmp_str):
        print_test_epilogue(test_name, f'Failed: ROUTe:CLOSe:STATe? Got {res}; Expected {cmp_str}')
        return False
    cmp_str = cmp_str.replace("1", "2")
    cmp_str = cmp_str.replace("0", "1")
    cmp_str = cmp_str.replace("2", "0")
    res = inst.query(f'ROUTe:OPEN? {chan_lst}')
    if (res != cmp_str):
        print_test_epilogue(test_name, f'Failed: ROUTe:OPEN? Got {res}; Expected {cmp_str}')
        return False
    return True

# Helper function to verify channel states (with range argument)
def verify_channel_states_range(inst, test_name, closed_channels):
    """
    Verify that specified channels are closed and all others are open.
    Args:
        inst: pyvisa Resource object for current running test
        test_name: String containing the name of current running test
        closed_channels: List of channels that should be closed (format: 'X!Y')
    Returns:
        bool: True if all channel states are as expected, False otherwise
    """
    closed_set = set(closed_channels)
    # Check all 16 possible channels in 4x4 matrix
    channels = []
    is_closed = []
    for input_num in range(4):
        for output_num in range(4):
            channels.append(f"{input_num}!{output_num}")
    for channel in channels:
        is_closed.append(channel in closed_set)
    chan_lst = "(@0!0:3!3)"
    cmp_str = ""
    for ic in is_closed:
        if ic is True:
            cmp_str += "1"
        else:
            cmp_str += "0"
        cmp_str += ","
    cmp_str = cmp_str[0:len(cmp_str)-1] # remove last comma
    res = inst.query(f'ROUTe:CLOSe:STATe? {chan_lst}')
    if (res != cmp_str):
        print_test_epilogue(test_name, f'Failed: ROUTe:CLOSe:STATe? Got {res}; Expected {cmp_str}')
        return False
    cmp_str = cmp_str.replace("1", "2")
    cmp_str = cmp_str.replace("0", "1")
    cmp_str = cmp_str.replace("2", "0")
    res = inst.query(f'ROUTe:OPEN? {chan_lst}')
    if (res != cmp_str):
        print_test_epilogue(test_name, f'Failed: ROUTe:OPEN? Got {res}; Expected {cmp_str}')
        return False
    return True

# unit tests

# connect and disconnect test
def unit_test_conn_disconn(rm, test_name = "Connect & disconnect test"):
    print_test_prologue(test_name)
    inst = None
    try:
        inst = resource_connect(rm)
        resource_disconnect(inst)
        print_test_epilogue(test_name)
    except Exception as e:
        print_test_epilogue(test_name, f"Exception: {str(e)}")
        if inst is not None:
            try:
                resource_disconnect(inst)
            except:
                pass

# *IDN? query test
def unit_test_idn_query(rm, test_name = "IDN query test"):
    print_test_prologue(test_name)
    inst = None
    try:
        inst = resource_connect(rm)
        idn = inst.query('*IDN?')
        print(f"*IDN? -> {idn}")
        resource_disconnect(inst)
        print_test_epilogue(test_name)
    except Exception as e:
        print_test_epilogue(test_name, f"Exception: {str(e)}")
        if inst is not None:
            try:
                resource_disconnect(inst)
            except:
                pass

# SCPI-99 ROUTe subsystem for 4x4 cross-point matrix switch test
def unit_test_route_open(rm, test_name="SCPI-99 ROUTe:OPEN:ALL"):
    print_test_prologue(test_name)
    inst = None
    try:
        inst = resource_connect(rm)

        # Test all valid bijections
        # Generate all (4! = 24) valid permutations (bijections)

        inputs = [0, 1, 2, 3]
        valid_permutations = []

        for perm in itertools.permutations(inputs):
            # perm[i] = output connected to input i
            connections = [f"@{i}!{perm[i]}" for i in inputs]
            valid_permutations.append(connections)

        print("Opening all channels")
        inst.write('ROUTe:OPEN:ALL')

        if not verify_channel_states(inst, test_name, []):
            resource_disconnect(inst)
            return

        resource_disconnect(inst)
        print_test_epilogue(test_name)
    except Exception as e:
        print_test_epilogue(test_name, f"Exception: {str(e)}")
        if inst is not None:
            try:
                resource_disconnect(inst)
            except:
                pass

# SCPI-99 ROUTe subsystem for 4x4 cross-point matrix switch test
def unit_test_route_close_valid(rm, test_name="SCPI-99 valid ROUTe:CLOSe"):
    print_test_prologue(test_name)
    inst = None
    try:
        inst = resource_connect(rm)

        # Test all valid bijections
        # Generate all (4! = 24) valid permutations (bijections)

        inputs = [0, 1, 2, 3]
        valid_permutations = []

        for perm in itertools.permutations(inputs):
            # perm[i] = output connected to input i
            connections = [f"@{i}!{perm[i]}" for i in inputs]
            valid_permutations.append(connections)

        print(f"Testing all {len(valid_permutations)} valid permutations (bijections)")

        for idx, connections in enumerate(valid_permutations, 1):
            # Open all channels before each test for clean state
            inst.write('ROUTe:OPEN:ALL')

            # Close all 4 channels for this permutation
            for channel in connections:
                inst.write(f'ROUTe:CLOSe ({channel})')

            # Verify states for this permutation
            if not verify_channel_states(inst, test_name, connections):
                print(f"Failed at permutation {idx}/{len(valid_permutations)}: {connections}")
                resource_disconnect(inst)
                return

            # uncomment to print progress
            """
            if idx % 6 == 0 or idx == len(valid_permutations):
                print(f"Tested {idx}/{len(valid_permutations)} permutations")
            """

        # Final cleanup: open all channels
        inst.write('ROUTe:OPEN:ALL')

        resource_disconnect(inst)
        print_test_epilogue(test_name)
    except Exception as e:
        print_test_epilogue(test_name, f"Exception: {str(e)}")
        if inst is not None:
            try:
                resource_disconnect(inst)
            except:
                pass

# SCPI-99 ROUTe subsystem for 4x4 cross-point matrix switch test
def unit_test_route_close_invalid(rm, test_name="SCPI-99 invalid ROUTe:CLOSe"):
    print_test_prologue(test_name)
    inst = None
    try:
        inst = resource_connect(rm)

        # Test all valid bijections
        # Generate all (4! = 24) valid permutations (bijections)

        inputs = [0, 1, 2, 3]
        valid_permutations = []

        for perm in itertools.permutations(inputs):
            # perm[i] = output connected to input i
            connections = [f"@{i}!{perm[i]}" for i in inputs]
            valid_permutations.append(connections)

        # Test 3a: Same input used twice
        print("  Testing: Same input used twice (@0!0 and @0!1)")
        inst.write('ROUTe:OPEN:ALL')

        inst.write('ROUTe:CLOSe (@0!0)')
        inst.write('ROUTe:CLOSe (@0!1)')

        # In a properly implemented cross-point switch, the second closure should fail
        # or the first should be opened. Verify that we don't have both closed.
        state0 = inst.query('ROUTe:CLOSe:STATe? (@0!0)').strip()
        state1 = inst.query('ROUTe:CLOSe:STATe? (@0!1)').strip()

        if state0 == '1' and state1 == '1':
            print_test_epilogue(test_name, 
                'Failed: Instrument allowed both @0!0 and @0!1 to be closed simultaneously '
                '(same input 0) - violates cross-point matrix constraints')
            resource_disconnect(inst)
            return

        print("    Same input conflict correctly handled")

        # Test 3b: Same output used twice
        print("  Testing: Same output used twice (@0!0 and @1!0)")
        inst.write('ROUTe:OPEN:ALL')

        inst.write('ROUTe:CLOSe (@0!0)')
        inst.write('ROUTe:CLOSe (@1!0)')

        state0 = inst.query('ROUTe:CLOSe:STATe? (@0!0)').strip()
        state1 = inst.query('ROUTe:CLOSe:STATe? (@1!0)').strip()

        if state0 == '1' and state1 == '1':
            print_test_epilogue(test_name, 
                'Failed: Instrument allowed both @0!0 and @1!0 to be closed simultaneously '
                '(same output 0) - violates cross-point matrix constraints')
            resource_disconnect(inst)
            return

        print("    Same output conflict correctly handled")
        print("  Invalid configuration handling passed")

        # Final cleanup: open all channels
        inst.write('ROUTe:OPEN:ALL')

        resource_disconnect(inst)
        print_test_epilogue(test_name)
    except Exception as e:
        print_test_epilogue(test_name, f"Exception: {str(e)}")
        if inst is not None:
            try:
                resource_disconnect(inst)
            except:
                pass

# ROUTe:CLOSe (@x!y,z!u) syntax check
def unit_test_route_close_enum_valid(rm, test_name = "SCPI-99 valid ROUTe:CLOSe enum"):
    print_test_prologue(test_name)
    inst = None
    try:
        inst = resource_connect(rm)

        # Test all valid bijections
        # Generate all (4! = 24) valid permutations (bijections)

        inputs = [0, 1, 2, 3]
        valid_permutations = []

        for perm in itertools.permutations(inputs):
            # perm[i] = output connected to input i
            connections = [f"{i}!{perm[i]}" for i in inputs]
            valid_permutations.append(connections)

        print(f"Testing all {len(valid_permutations)} valid permutations (bijections)")

        for idx, connections in enumerate(valid_permutations, 1):
            # Open all channels before each test for clean state
            inst.write('ROUTe:OPEN:ALL')

            # Close all 4 channels for this permutation
            inst.write(f'ROUTe:CLOSe (@{connections[0]},{connections[1]},{connections[2]},{connections[3]})')

            # Verify states for this permutation
            if not verify_channel_states_enum(inst, test_name, connections):
                print(f"Failed at permutation {idx}/{len(valid_permutations)}: {connections}")
                resource_disconnect(inst)
                return

        resource_disconnect(inst)
        print_test_epilogue(test_name)
    except Exception as e:
        print_test_epilogue(test_name, f"Exception: {str(e)}")
        if inst is not None:
            try:
                resource_disconnect(inst)
            except:
                pass

# ROUTe:CLOSe:STATe? (@0!0:3!3) syntax check
def unit_test_route_closeq_range_valid(rm, test_name = "SCPI-99 valid ROUTe:CLOSe:STATe? range"):
    print_test_prologue(test_name)
    inst = None
    try:
        inst = resource_connect(rm)

        # Test all valid bijections
        # Generate all (4! = 24) valid permutations (bijections)

        inputs = [0, 1, 2, 3]
        valid_permutations = []

        for perm in itertools.permutations(inputs):
            # perm[i] = output connected to input i
            connections = [f"{i}!{perm[i]}" for i in inputs]
            valid_permutations.append(connections)

        print(f"Testing all {len(valid_permutations)} valid permutations (bijections)")

        for idx, connections in enumerate(valid_permutations, 1):
            # Open all channels before each test for clean state
            inst.write('ROUTe:OPEN:ALL')

            # Close all 4 channels for this permutation
            inst.write(f'ROUTe:CLOSe (@{connections[0]},{connections[1]},{connections[2]},{connections[3]})')

            # Verify states for this permutation
            if not verify_channel_states_range(inst, test_name, connections):
                print(f"Failed at permutation {idx}/{len(valid_permutations)}: {connections}")
                resource_disconnect(inst)
                return

        resource_disconnect(inst)
        print_test_epilogue(test_name)
    except Exception as e:
        print_test_epilogue(test_name, f"Exception: {str(e)}")
        if inst is not None:
            try:
                resource_disconnect(inst)
            except:
                pass

if __name__ == '__main__':
    main()
