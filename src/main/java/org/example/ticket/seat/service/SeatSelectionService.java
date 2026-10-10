package org.example.ticket.seat.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.seat.dto.SeatResponse;
import org.example.ticket.seat.dto.SeatSelectionRequest;
import org.example.ticket.seat.dto.SeatSelectionSummaryResponse;
import org.example.ticket.seat.repository.SeatRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@RequiredArgsConstructor
public class SeatSelectionService {

    private final SeatRepository seatRepository;

    @Transactional(readOnly = true)
    public SeatSelectionSummaryResponse summarize(SeatSelectionRequest request) {
        List<Long> seatIds = request.seatIds().stream().distinct().sorted().toList();
        if (seatIds.size() != request.seatIds().size()) {
            throw new BusinessException(HttpStatus.BAD_REQUEST, "DUPLICATED_SEAT", "중복 좌석이 포함되어 있습니다.");
        }

        List<SeatResponse> seats = seatRepository.findSeatSelection(request.performanceTimeId(), seatIds);
        if (seats.size() != seatIds.size()) {
            throw new BusinessException(HttpStatus.BAD_REQUEST, "SEAT_NOT_FOUND",
                    "공연 회차에 속하지 않는 좌석이 포함되어 있습니다.");
        }

        return SeatSelectionSummaryResponse.of(request.performanceTimeId(), seats);
    }
}
