# Power Monitoring Feature

## Tổng quan (Overview)

Tính năng này sử dụng lệnh `vcgencmd pmic_read_adc` để đọc điện áp và dòng điện từ các nhánh nguồn của Raspberry Pi PMIC (Power Management IC), sau đó tính tổng công suất tiêu thụ.

This feature uses the `vcgencmd pmic_read_adc` command to read voltage and current from Raspberry Pi PMIC (Power Management IC) power rails, then calculates total power consumption.

## Các thay đổi (Changes Made)

### 1. Cập nhật SystemStats class (`rpi_support.py`)

Thêm các trường mới:
- `total_power_w`: Tổng công suất tiêu thụ (W)
- `pmic_voltages`: Dictionary chứa điện áp các nhánh
- `pmic_currents`: Dictionary chứa dòng điện các nhánh

```python
@dataclass
class SystemStats:
    # ... existing fields ...
    #POWER
    total_power_w: float = 0.0
    pmic_voltages: dict = field(default_factory=dict)
    pmic_currents: dict = field(default_factory=dict)
```

### 2. Cập nhật get_system_stat() function

Thêm phần đọc PMIC ADC:

```python
#POWER - Read PMIC ADC values
try:
    raw_result = subprocess.run(["vcgencmd", "pmic_read_adc"], 
                               capture_output=True, text=True)
    pmic_output = raw_result.stdout.strip()
    
    # Parse và tính toán công suất
    # Parse output and calculate power
    ...
```

### 3. Thêm print_power_info() function

Hàm hiển thị thông tin công suất ra console:

```python
def print_power_info(sysStats):
    """Print power consumption information to console"""
    # Hiển thị bảng với điện áp, dòng điện, công suất của từng nhánh
    # Display table with voltage, current, power for each rail
```

### 4. Cập nhật main loop

Thêm code in thông tin công suất mỗi 5 giây:

```python
# Print power info every 5 seconds
if (time.perf_counter() - power_print_time) > 5:
    power_print_time = time.perf_counter()
    print_power_info(sysStats)
```

## Cách sử dụng (Usage)

### Chạy script chính (Run main script):

```bash
sudo python3 rpi_support.py
```

### Chạy script test (Run test script):

```bash
sudo python3 test_power.py
```

**Lưu ý**: Cần quyền sudo để chạy `vcgencmd`

## Công thức tính toán (Calculation Formula)

Công suất tiêu thụ được tính theo công thức:

Power consumption is calculated using the formula:

```
P (W) = V (V) × I (A)
```

Tổng công suất = tổng công suất của tất cả các nhánh nguồn

Total power = sum of power from all power rails

## Ví dụ đầu ra (Example Output)

```
==================================================
RASPBERRY PI POWER CONSUMPTION
==================================================

Power Rails:
--------------------------------------------------
EXT5V                | V:  5.023V | I:  0.500A | P:  2.512W
BATT                 | V:  3.700V | I:  0.200A | P:  0.740W
--------------------------------------------------
TOTAL POWER          |  3.252W
==================================================
```

## Lưu ý quan trọng (Important Notes)

1. **Hỗ trợ thiết bị**: Không phải tất cả các model Raspberry Pi đều hỗ trợ `vcgencmd pmic_read_adc`
   - Pi 5 và một số model mới hơn hỗ trợ đầy đủ
   - Các model cũ có thể không có lệnh này

2. **Quyền truy cập**: Cần chạy với quyền sudo để truy cập PMIC

3. **Định dạng đầu ra**: Định dạng của `vcgencmd pmic_read_adc` có thể khác nhau tùy theo model Pi

4. **Độ chính xác**: Giá trị đo được từ PMIC có thể có sai số nhỏ so với đo lường bằng thiết bị chuyên dụng

## Device Support

1. **Device Support**: Not all Raspberry Pi models support `vcgencmd pmic_read_adc`
   - Pi 5 and some newer models have full support
   - Older models may not have this command

2. **Permissions**: Requires sudo privileges to access PMIC

3. **Output Format**: The format of `vcgencmd pmic_read_adc` may vary by Pi model

4. **Accuracy**: PMIC measurements may have small errors compared to dedicated measurement equipment
