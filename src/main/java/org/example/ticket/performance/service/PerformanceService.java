package org.example.ticket.performance.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.performance.domain.*;
import org.example.ticket.performance.dto.*;
import org.example.ticket.performance.repository.*;
import org.example.ticket.seat.service.SeatProvisioningService;
import org.example.ticket.venue.domain.VenueHall;
import org.example.ticket.venue.repository.VenueHallRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class PerformanceService {
    private final PerformanceRepository performanceRepository;
    private final PerformanceTimeRepository performanceTimeRepository;
    private final SeatPriceRepository seatPriceRepository;
    private final VenueHallRepository venueHallRepository;
    private final SeatProvisioningService seatProvisioningService;

    @Transactional
    public Long createPerformance(CreatePerformanceRequest request) {
        Performance performance = performanceRepository.save(Performance.builder().title(request.title()).build());
        seatPriceRepository.saveAll(request.prices().entrySet().stream()
                .map(e -> SeatPrice.builder().performance(performance).grade(e.getKey()).price(e.getValue()).build())
                .toList());
        return performance.getId();
    }

    @Transactional
    public Long createPerformanceTime(Long performanceId, CreatePerformanceTimeRequest request) {
        Performance performance = performanceRepository.findById(performanceId).orElseThrow(() -> notFound("공연"));
        VenueHall hall = venueHallRepository.findById(request.venueHallId()).orElseThrow(() -> notFound("공연장"));
        PerformanceTime time = performanceTimeRepository.save(PerformanceTime.builder()
                .performance(performance).venueHall(hall).startsAt(request.startsAt()).build());
        seatProvisioningService.createSeats(time);
        return time.getId();
    }

    private BusinessException notFound(String target) {
        return new BusinessException(HttpStatus.NOT_FOUND, "NOT_FOUND", target + "을 찾을 수 없습니다.");
    }
}
