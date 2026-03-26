import env
import time
import pyvisa
import itertools

# Aliases for inputs (rows)
class Input:
    VOL_POSITIVE = 0   # Vol positive input
    VOL_NEGATIVE = 1   # Vol negative input
    AMP_POSITIVE = 2   # Amp positive input
    AMP_NEGATIVE = 3   # Amp negative input

# Aliases for outputs (columns)
class Output:
    CHANNEL_A = 0
    CHANNEL_B = 1
    CHANNEL_C = 2
    CHANNEL_D = 3

# Human-readable names for debugging/display
INPUT_NAMES = {
    Input.VOL_POSITIVE: "VOL+",
    Input.VOL_NEGATIVE: "VOL-",
    Input.AMP_POSITIVE: "AMP+",
    Input.AMP_NEGATIVE: "AMP-"
}

OUTPUT_NAMES = {
    Output.CHANNEL_A: "CH A",
    Output.CHANNEL_B: "CH B",
    Output.CHANNEL_C: "CH C",
    Output.CHANNEL_D: "CH D"
}

# Helper function to create channel string
def make_channel(input_num, output_num):
    """
    Create channel string in format "@X!Y"
    
    Args:
        input_num: Input number (use Input class constants)
        output_num: Output number (use Output class constants)
    
    Returns:
        String like "@0!2"
    """
    return f"@{input_num}!{output_num}"

# Helper function to parse channel string
def parse_channel(channel_str):
    """
    Parse channel string and return input and output numbers
    
    Args:
        channel_str: String like "@0!2"
    
    Returns:
        Tuple (input_num, output_num)
    """
    parts = channel_str[1:].split('!')
    return int(parts[0]), int(parts[1])

# Helper function to get human-readable channel description
def describe_channel(channel_str):
    """
    Get human-readable description of a channel
    
    Args:
        channel_str: String like "@0!2"
    
    Returns:
        String like "VOL+ -> CH C"
    """
    input_num, output_num = parse_channel(channel_str)
    input_name = INPUT_NAMES.get(input_num, f"IN{input_num}")
    output_name = OUTPUT_NAMES.get(output_num, f"OUT{output_num}")
    return f"{input_name} -> {output_name}"

class MatrixSwitchController:
    """Controller for 4x4 cross-point matrix switch with sequential connection patterns"""
    
    def __init__(self, rm=None):
        """
        Initialize the controller
        
        Args:
            rm: pyvisa ResourceManager instance (creates new if None)
        """
        self.rm = rm if rm is not None else pyvisa.ResourceManager()
        self.inst = None
        self.current_index = 0
        self.patterns = self._generate_all_patterns()
        self.callback = None
        
    def _generate_all_patterns(self):
        """
        Generate all valid connection patterns (bijections between inputs and outputs)
        
        Returns:
            List of patterns, where each pattern is a list of 4 connections in format "@X!Y"
        """
        inputs = [Input.VOL_POSITIVE, Input.VOL_NEGATIVE, Input.AMP_POSITIVE, Input.AMP_NEGATIVE]
        patterns = []
        
        for perm in itertools.permutations([Output.CHANNEL_A, Output.CHANNEL_B, 
                                            Output.CHANNEL_C, Output.CHANNEL_D]):
            # perm[i] = output connected to input i
            connections = [make_channel(inputs[i], perm[i]) for i in range(len(inputs))]
            patterns.append(connections)
            
        return patterns
    
    def connect(self):
        """Establish connection to the instrument"""
        print(f"Connecting to {env.VISA_RESOURCE_NAME}")
        self.inst = self.rm.open_resource(env.VISA_RESOURCE_NAME, read_termination="\r\n")
        self.inst.query_delay = env.VISA_QUERY_DELAY_SEC
        time.sleep(env.VISA_QUERY_DELAY_SEC)
        
    def disconnect(self):
        """Close connection to the instrument"""
        if self.inst:
            print(f"Disconnecting from {env.VISA_RESOURCE_NAME}")
            self.inst.close()
            self.inst = None
            
    def open_all_channels(self):
        """Open all channels in the matrix"""
        if not self.inst:
            raise Exception("Not connected to instrument")
        self.inst.write('ROUTe:OPEN:ALL')
        time.sleep(env.VISA_QUERY_DELAY_SEC)
        
    def set_pattern(self, pattern):
        """
        Set a specific connection pattern
        
        Args:
            pattern: List of channel connections in format "@X!Y"
        """
        if not self.inst:
            raise Exception("Not connected to instrument")
            
        # Open all channels first to ensure clean state
        self.open_all_channels()
        
        # Close each channel in the pattern
        for channel in pattern:
            self.inst.write(f'ROUTe:CLOSe ({channel})')
            
        time.sleep(env.VISA_QUERY_DELAY_SEC)
        
    def set_custom_pattern(self, connections):
        """
        Set a custom pattern using input and output aliases
        
        Args:
            connections: List of tuples (input_alias, output_alias)
                        Example: [(Input.VOL_POSITIVE, Output.CHANNEL_A),
                                  (Input.VOL_NEGATIVE, Output.CHANNEL_B),
                                  (Input.AMP_POSITIVE, Output.CHANNEL_C),
                                  (Input.AMP_NEGATIVE, Output.CHANNEL_D)]
        """
        pattern = [make_channel(inp, out) for inp, out in connections]
        self.set_pattern(pattern)
        
    def set_pattern_by_index(self, index):
        """
        Set pattern by its index in the patterns list
        
        Args:
            index: Index of the pattern to set
        """
        if index < 0 or index >= len(self.patterns):
            raise IndexError(f"Pattern index {index} out of range (0-{len(self.patterns)-1})")
            
        pattern = self.patterns[index]
        self.set_pattern(pattern)
        
        # Call the callback function if it exists
        if self.callback:
            self.callback(index, pattern)
            
    def next_pattern(self):
        """
        Move to the next pattern (cyclic)
        """
        self.current_index = (self.current_index + 1) % len(self.patterns)
        self.set_pattern_by_index(self.current_index)
        
    def previous_pattern(self):
        """
        Move to the previous pattern (cyclic)
        """
        self.current_index = (self.current_index - 1) % len(self.patterns)
        self.set_pattern_by_index(self.current_index)
        
    def run_full_cycle(self):
        """
        Run through all patterns exactly once, from current position
        Returns to the original pattern after completion
        """
        if not self.inst:
            raise Exception("Not connected to instrument")
            
        start_index = self.current_index
        total_patterns = len(self.patterns)
        
        print(f"\n=== Running full cycle of {total_patterns} patterns ===")
        
        for i in range(total_patterns):
            pattern_index = (start_index + i) % total_patterns
            print(f"\n--- Pattern {pattern_index + 1}/{total_patterns} ---")
            self.set_pattern_by_index(pattern_index)
            
        # Restore original pattern
        self.set_pattern_by_index(start_index)
        print("\n=== Full cycle completed ===")
        
    def get_current_pattern(self):
        """Get the current pattern"""
        return self.patterns[self.current_index]
        
    def get_total_patterns(self):
        """Get total number of patterns"""
        return len(self.patterns)
        
    def set_callback(self, callback_func):
        """
        Set callback function to be called after each pattern change
        
        Args:
            callback_func: Function that takes (index, pattern) as arguments
        """
        self.callback = callback_func
        
    def reorder_patterns(self, new_order):
        """
        Reorder the patterns list
        
        Args:
            new_order: List of pattern indices in the desired order
        """
        if len(new_order) != len(self.patterns):
            raise ValueError(f"New order must have {len(self.patterns)} elements")
            
        if set(new_order) != set(range(len(self.patterns))):
            raise ValueError("New order must contain all pattern indices exactly once")
            
        self.patterns = [self.patterns[i] for i in new_order]
        # Reset current index if it's out of range
        if self.current_index >= len(self.patterns):
            self.current_index = 0
            
    def get_patterns_list(self):
        """
        Get the list of all patterns (for inspection or reordering)
        
        Returns:
            List of pattern lists
        """
        return self.patterns.copy()
        
    def print_pattern(self, pattern):
        """Pretty print a pattern with human-readable names"""
        descriptions = []
        for channel in pattern:
            descriptions.append(describe_channel(channel))
        return ", ".join(descriptions)
        
    def print_current_pattern(self):
        """Print the current pattern in human-readable format"""
        pattern = self.get_current_pattern()
        print(f"Current pattern: {self.print_pattern(pattern)}")


# Example callback function - you can modify this as needed
def on_pattern_change(pattern_index, pattern):
    """
    Callback function called after each pattern change
    
    Args:
        pattern_index: Index of the current pattern
        pattern: List of channel connections in format "@X!Y"
    """
    # This is where you can add your custom code
    # Example: Print pattern with human-readable names
    controller = globals().get('controller')  # Get controller instance if available
    if controller:
        print(f"\n=== Pattern {pattern_index} changed ===")
        print(f"Connections:")
        for channel in pattern:
            print(f"  {describe_channel(channel)}")
    else:
        print(f"Callback: Pattern {pattern_index} set: {pattern}")


def main():
    """Main function demonstrating the controller usage"""
    global controller  # Make controller available for callback
    
    rm = pyvisa.ResourceManager()
    controller = MatrixSwitchController(rm)
    
    try:
        # Connect to the instrument
        controller.connect()
        
        # Set the callback function
        controller.set_callback(on_pattern_change)
        
        print("\n=== Using aliases for custom patterns ===")
        
        # Example 1: Set custom pattern using aliases
        print("\nSetting custom pattern:")
        custom_connections = [
            (Input.VOL_POSITIVE, Output.CHANNEL_A),   # VOL+ -> CH A
            (Input.VOL_NEGATIVE, Output.CHANNEL_B),   # VOL- -> CH B
            (Input.AMP_POSITIVE, Output.CHANNEL_C),   # AMP+ -> CH C
            (Input.AMP_NEGATIVE, Output.CHANNEL_D)    # AMP- -> CH D
        ]
        controller.set_custom_pattern(custom_connections)
        controller.print_current_pattern()
        
        time.sleep(1)
        
        # Example 2: Another custom pattern
        print("\nSetting another custom pattern:")
        custom_connections2 = [
            (Input.VOL_POSITIVE, Output.CHANNEL_D),   # VOL+ -> CH D
            (Input.VOL_NEGATIVE, Output.CHANNEL_C),   # VOL- -> CH C
            (Input.AMP_POSITIVE, Output.CHANNEL_B),   # AMP+ -> CH B
            (Input.AMP_NEGATIVE, Output.CHANNEL_A)    # AMP- -> CH A
        ]
        controller.set_custom_pattern(custom_connections2)
        controller.print_current_pattern()
        
        time.sleep(1)
        
        # Example 3: Test specific connections using make_channel helper
        print("\nTesting specific channel:")
        test_channel = make_channel(Input.VOL_POSITIVE, Output.CHANNEL_A)
        print(f"Channel string: {test_channel}")
        print(f"Description: {describe_channel(test_channel)}")
        
        # Example 4: Manual navigation through patterns
        print(f"\n=== Manual navigation through {controller.get_total_patterns()} patterns ===")
        
        # Show first few patterns
        for i in range(3):
            print(f"\nPattern {i}:")
            controller.set_pattern_by_index(i)
            controller.print_current_pattern()
            time.sleep(0.5)
        
        # Example 5: Run full cycle (optional - uncomment to run)
        # print("\n=== Running full cycle ===")
        # controller.run_full_cycle()
        
        # Wait a moment before disconnecting
        time.sleep(1)
        
    except Exception as e:
        print(f"Error: {e}")
        
    finally:
        # Clean up
        controller.disconnect()


# Additional utility functions for working with aliases
def get_channel_info(channel_str):
    """
    Get detailed information about a channel
    
    Args:
        channel_str: Channel string like "@0!2"
    
    Returns:
        Dictionary with input_num, output_num, input_name, output_name
    """
    input_num, output_num = parse_channel(channel_str)
    return {
        'input_num': input_num,
        'output_num': output_num,
        'input_name': INPUT_NAMES.get(input_num, f"UNKNOWN_IN_{input_num}"),
        'output_name': OUTPUT_NAMES.get(output_num, f"UNKNOWN_OUT_{output_num}"),
        'channel_string': channel_str
    }


if __name__ == '__main__':
    main()
