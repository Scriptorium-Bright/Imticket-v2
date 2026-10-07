package org.example.ticket.seat.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.performance.domain.*;
import org.example.ticket.performance.repository.SeatPriceRepository;
import org.example.ticket.seat.domain.*;
import org.example.ticket.seat.repository.SeatRepository;
import org.example.ticket.venue.domain.*;
import org.example.ticket.venue.repository.VenueHallSeatTemplateRepository;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class SeatProvisioningService {
    private final VenueHallSeatTemplateRepository templateRepository;
    private final SeatPriceRepository seatPriceRepository;
    private final SeatRepository seatRepository;

    public void createSeats(PerformanceTime performanceTime) {
        Map<SeatGrade,Integer> prices = seatPriceRepository.findAllByPerformanceId(performanceTime.getPerformance().getId())
                .stream().collect(Collectors.toMap(SeatPrice::getGrade, SeatPrice::getPrice));
        var seats = templateRepository.findAllByVenueHallIdOrderByRowNumberAscSeatNumberAsc(performanceTime.getVenueHall().getId())
                .stream().map(t -> toSeat(performanceTime, t, prices)).toList();
        seatRepository.saveAll(seats);
    }

    private Seat toSeat(PerformanceTime time, VenueHallSeatTemplate template, Map<SeatGrade,Integer> prices) {
        Integer price = prices.get(template.getGrade());
        if (price == null) throw new IllegalArgumentException("좌석 등급 가격이 없습니다: " + template.getGrade());
        return Seat.builder().performanceTime(time).section(template.getSection())
                .rowNumber(template.getRowNumber()).seatNumber(template.getSeatNumber())
                .grade(template.getGrade()).price(price).status(SeatStatus.AVAILABLE).build();
    }
}
