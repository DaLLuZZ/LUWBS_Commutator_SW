import env
import time
import pyvisa

def main():
    rm = pyvisa.ResourceManager()
    
    # Массив последовательности замыкания каналов
    # Каждый элемент - список каналов для замыкания
    # Формат канала: "X!Y" где X - вход (0-3), Y - выход (0-3)

    # Допустим подключили так:
    # Voltage 1 pos - вход 0
    # Voltage 1 neg - вход 1
    # Amper 1 pos - вход 2
    closure_sequence = [
        ["0!0", "1!1", "2!2", "3!3"],
        ["0!0", "1!1", "2!2", "3!3"],
        ["0!0", "1!1", "2!2", "3!3"],
    ]
    
    run_switching_sequence(rm, closure_sequence)

    input()















def run_switching_sequence(rm, sequence, program_name="Управление матричным коммутатором"):
    print(f"\nЗапуск: {program_name}")
    inst = None
    
    try:
        inst = connect_device(rm)
        
        # Открыть все каналы
        inst.write('ROUTe:OPEN:ALL')
        print("Все каналы открыты")

        input()
        
        # Циклически проходим по массиву последовательности
        step_number = 0
        total_steps = len(sequence)
        
        while True:
            # Получаем текущий набор каналов
            channels_to_close = sequence[step_number % total_steps]
            
            print(f"\nШаг {step_number + 1}: Замыкание каналов {channels_to_close}")
            
            # Закрываем каналы
            channel_list = "(@" + ",".join(channels_to_close) + ")"
            inst.write(f'ROUTe:CLOSe {channel_list}')
            
            # Проверяем состояние
            if verify_channels(inst, channels_to_close):
                print(f"Каналы {channels_to_close} замкнуты")
            else:
                print(f"Ошибка при замыкании каналов {channels_to_close}")
                break
            
            # Ожидание перед следующим переключением
            input("Нажмите Enter для следующего переключения...")
            
            # Открываем каналы перед следующим шагом
            inst.write('ROUTe:OPEN:ALL')
            
            step_number += 1
            
            # Опционально: ограничить количество циклов
            # if step_number >= total_steps * 2:  # 2 цикла
            #     break
        
        # Финальная очистка
        inst.write('ROUTe:OPEN:ALL')
        print("\nВсе каналы открыты")
        
        disconnect_device(inst)
        print(f"Программа '{program_name}' завершена\n")
        
    except Exception as e:
        print(f"Ошибка: {str(e)}")
        if inst is not None:
            try:
                disconnect_device(inst)
            except:
                pass

# Вспомогательные функции

def connect_device(rm):
    print(f"Подключение к {env.VISA_RESOURCE_NAME}")
    inst = rm.open_resource(env.VISA_RESOURCE_NAME, read_termination="\r\n")
    inst.query_delay = env.VISA_QUERY_DELAY_SEC
    time.sleep(env.VISA_CONNECT_DELAY_SEC)
    return inst

def disconnect_device(inst):
    print(f"Отключение от {env.VISA_RESOURCE_NAME}")
    inst.close()

def verify_channels(inst, expected_closed_channels):
    """
    Проверяет, что указанные каналы замкнуты, а остальные разомкнуты
    """
    expected_closed_set = set(expected_closed_channels)
    
    # Проверяем все 16 каналов
    for input_num in range(4):
        for output_num in range(4):
            channel = f"{input_num}!{output_num}"
            is_expected_closed = channel in expected_closed_set
            
            # Проверяем состояние через ROUTe:CLOSe:STATe?
            state = inst.query(f'ROUTe:CLOSe:STATe? (@{channel})')
            is_closed = (state.strip() == '1')
            
            if is_expected_closed != is_closed:
                print(f"Ошибка: Канал {channel} должен быть {'замкнут' if is_expected_closed else 'разомкнут'}, "
                      f"но ROUTe:CLOSe:STATe? вернул '{state}'")
                return False
    
    return True

if __name__ == '__main__':
    main()
