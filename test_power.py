#!/usr/bin/env python3
"""
Test script to demonstrate vcgencmd pmic_read_adc power calculation
This script reads voltage and current from PMIC and calculates total power consumption
"""

import subprocess
import time

def read_pmic_power():
    """Read PMIC ADC values and calculate power consumption"""
    try:
        # Run vcgencmd pmic_read_adc
        result = subprocess.run(["vcgencmd", "pmic_read_adc"], 
                              capture_output=True, text=True, check=True)
        pmic_output = result.stdout.strip()
        
        print("\n" + "="*60)
        print("RAW PMIC OUTPUT:")
        print("="*60)
        print(pmic_output)
        print("="*60)
        
        total_power = 0.0
        voltages = {}
        currents = {}
        
        # Parse PMIC output
        # The format may vary between Raspberry Pi models
        # Common formats:
        # - EXT5V_V=5.0234V EXT5V_I=0.5000A
        # - BATT_V=3.7V BATT_I=0.2A
        for line in pmic_output.split('\n'):
            parts = line.split()
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    try:
                        # Remove units (V, A, mA, mV, W, mW)
                        val_str = value.rstrip('VAmWvw')
                        val_float = float(val_str)
                        
                        # Handle millivolts/milliamps
                        if value.endswith('mV'):
                            val_float = val_float / 1000.0
                        elif value.endswith('mA'):
                            val_float = val_float / 1000.0
                        
                        # Store voltage values
                        if '_V' in key or 'VOLT' in key.upper():
                            rail_name = key.replace('_V', '').replace('VOLT', '')
                            voltages[rail_name] = val_float
                        
                        # Store current values
                        elif '_I' in key or '_A' in key or 'CURR' in key.upper():
                            rail_name = key.replace('_I', '').replace('_A', '').replace('CURR', '')
                            currents[rail_name] = val_float
                            
                    except ValueError as e:
                        print(f"Warning: Could not parse '{part}': {e}")
                        continue
        
        # Display parsed values
        print("\n" + "="*60)
        print("PARSED POWER RAILS:")
        print("="*60)
        print(f"{'Rail Name':<20} | {'Voltage (V)':<12} | {'Current (A)':<12} | {'Power (W)':<12}")
        print("-"*60)
        
        # Calculate total power for each rail where we have both voltage and current
        all_rails = set(list(voltages.keys()) + list(currents.keys()))
        
        for rail in sorted(all_rails):
            voltage = voltages.get(rail, 0.0)
            current = currents.get(rail, 0.0)
            power = voltage * current
            total_power += power
            
            print(f"{rail:<20} | {voltage:>10.4f} V | {current:>10.4f} A | {power:>10.4f} W")
        
        print("-"*60)
        print(f"{'TOTAL POWER':<20} | {'':<12} | {'':<12} | {total_power:>10.4f} W")
        print("="*60 + "\n")
        
        return {
            'total_power_w': total_power,
            'voltages': voltages,
            'currents': currents
        }
        
    except subprocess.CalledProcessError as e:
        print(f"Error running vcgencmd: {e}")
        print("This command may not be supported on your Raspberry Pi model")
        return None
    except FileNotFoundError:
        print("vcgencmd not found. This script must be run on a Raspberry Pi")
        return None
    except Exception as e:
        print(f"Unexpected error: {e}")
        return None

if __name__ == "__main__":
    print("Raspberry Pi Power Monitor")
    print("Press Ctrl+C to exit")
    print()
    
    try:
        while True:
            power_data = read_pmic_power()
            
            if power_data:
                # Wait 2 seconds before next reading
                time.sleep(2)
            else:
                print("\nFailed to read power data. Exiting...")
                break
                
    except KeyboardInterrupt:
        print("\n\nExiting...")
